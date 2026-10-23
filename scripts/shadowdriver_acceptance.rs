use std::env;
use std::error::Error;
use std::future::Future;
use std::fs;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::Poll;
use std::time::Duration;

use miniserde::json::{self, Object, Value};
use shadowdriver::crawler::EventHandler;
use shadowdriver::{
    BlockPattern, BrowserIdentityMode, ChromiumSessionOptions, CrawlerSession, CrawlerTab,
    TabOptions,
};

#[path = "support/http.rs"]
mod http;

use http::{LocalHttpServer, Request, Response};

const PAGE_ADMISSION: usize = 8;

fn main() {
    if let Err(error) = async_io::block_on(run()) {
        eprintln!("shadowdriver acceptance failed: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn Error>> {
    let binary = PathBuf::from(env::var("CHROMIUM_TEST_EXECUTABLE")?);
    let profile = PathBuf::from(env::var("CHROMIUM_TEST_PROFILE")?);
    let ublock = PathBuf::from(env::var("CHROMIUM_TEST_EXTENSION")?);
    let (server, frame_url) = fixture_servers();
    let proxy_url = server.base_url();
    let mut options = ChromiumSessionOptions::new(binary.clone(), profile.clone());
    options.headless = true;
    options.persistent = false;
    options.identity_mode = BrowserIdentityMode::BrowserNative;
    options.stealth = true;
    options.extensions = vec![ublock.clone()];
    isolate_profile_network(&profile, &proxy_url)?;

    let session = CrawlerSession::ensure_chromium(options.clone()).await?;
    let provenance = session.provenance().await?;
    ensure(
        provenance.headless
            && provenance.identity_mode == BrowserIdentityMode::BrowserNative
            && provenance.extensions.len() == 1,
        "Shadowdriver did not retain the requested BrowserNative headless extension policy",
    )?;
    let targets = session.command("Target.getTargets", empty_object()).await?;
    let extension_url = match field(&targets, "targetInfos") {
        Some(Value::Array(targets)) => targets.iter().find_map(|target| {
            let url = field_text(target, "url")?;
            url.starts_with("chrome-extension://").then(|| url.to_owned())
        }),
        _ => None,
    }
    .ok_or("uBlock did not expose an active extension page")?;
    let extension_id = extension_url
        .strip_prefix("chrome-extension://")
        .and_then(|url| url.split('/').next())
        .ok_or("uBlock extension target had an invalid URL")?;
    let filters_tab = session.new_tab(TabOptions::default()).await?;
    filters_tab
        .navigate(
            &format!("chrome-extension://{extension_id}/3p-filters.html"),
            "complete",
            Duration::from_secs(15),
        )
        .await?;
    let filter_state = evaluate_text(
        &filters_tab,
        "JSON.stringify((()=>{const entry=document.querySelector('#lists .listEntry[data-key=\"easylist\"]');return {enabled:entry?.querySelector('input[type=checkbox]')?.checked,failed:entry?.querySelector('.status.failed')?.classList.contains('on')===true}})())",
    )
    .await?;
    let filter_state = json::from_str::<Value>(&filter_state)?;
    ensure(
        matches!(field(&filter_state, "enabled"), Some(Value::Bool(true)))
            && matches!(field(&filter_state, "failed"), Some(Value::Bool(false))),
        format!("uBlock EasyList was not ready: {}", serialize(&filter_state)),
    )?;
    filters_tab.close().await?;
    let core_result = run_browser_checks(&session, &server).await;
    if let Err(error) = &core_result {
        eprintln!("fixture origins: main={} frame=opaque data URL", server.base_url());
        eprintln!(
            "loopback requests before failure: {:?}",
            server
                .requests()
                .iter()
                .filter(|record| record.request.target.starts_with("/identity"))
                .map(|record| record.request.target.clone())
                .collect::<Vec<_>>()
        );
        eprintln!(
            "browser log before cleanup: {}",
            fs::read_to_string(profile.join("chromium.log")).unwrap_or_else(|read_error| read_error.to_string())
        );
        eprintln!("acceptance failure: {error}");
    }
    session.close(true).await;
    core_result?;

    isolate_profile_network(&profile, &proxy_url)?;
    let restarted = CrawlerSession::ensure_chromium(options.clone()).await?;
    let restart_result = async {
        let tab = restarted.new_tab(TabOptions::default()).await?;
        tab.navigate(
            &server.url("/page?index=restart"),
            "complete",
            Duration::from_secs(15),
        )
        .await?;
        let value = evaluate_json(
            &tab,
            "JSON.stringify({userAgent:navigator.userAgent,platform:navigator.platform})",
        )
        .await?;
        let user_agent = field_text(&value, "userAgent")
            .ok_or("restarted browser omitted navigator.userAgent")?;
        ensure(
            user_agent.contains("Chrome/") || user_agent.contains("Chromium/"),
            "restarted browser did not expose Chromium's native user agent",
        )?;
        tab.close().await?;
        let expected_user_agent = user_agent.to_owned();
        run_page_wave(&restarted, &expected_user_agent, 32).await?;
        run_page_wave(&restarted, &expected_user_agent, 64).await?;
        Ok::<(), Box<dyn Error>>(())
    }
    .await;
    if let Err(error) = &restart_result {
        let requests = server
            .requests()
            .iter()
            .map(|record| record.request.target.clone())
            .collect::<Vec<_>>();
        eprintln!("restart/page acceptance failure: {error}; fixture requests={requests:?}");
    }
    restarted.close(true).await;
    restart_result?;

    run_extension_acceptance(&binary, &profile, &server, &proxy_url, 2).await?;
    run_extension_acceptance(&binary, &profile, &server, &proxy_url, 3).await?;
    run_headed_acceptance(&binary, &profile, &server, &proxy_url).await?;

    println!(
        "shadowdriver acceptance passed: product={} revision={} network=loopback-fixture-and-proxy-sink browser_native=first-navigation,reload,new-target,restart,frame,dedicated-worker,shared-worker,service-worker cdp=contexts,console,exception,binding,response-body,interception,screenshot,pdf extensions=mv2,mv3 ublock=local-filter pages=32,64 admission={PAGE_ADMISSION} headed=true",
        provenance.product,
        provenance.revision
    );
    Ok(())
}

async fn run_browser_checks(
    session: &CrawlerSession,
    server: &LocalHttpServer,
) -> Result<(), Box<dyn Error>> {
    let tab = session.new_tab(TabOptions::default()).await?;
    eprintln!("core: created identity target at {}", server.url("/identity"));
    let execution_contexts = Arc::new(Mutex::new(Vec::new()));
    let console_events = Arc::new(Mutex::new(Vec::new()));
    let exception_events = Arc::new(Mutex::new(Vec::new()));
    let binding_events = Arc::new(Mutex::new(Vec::new()));
    let response_events = Arc::new(Mutex::new(Vec::new()));
    let request_events = Arc::new(Mutex::new(Vec::new()));
    let failed_requests = Arc::new(Mutex::new(Vec::new()));
    let _execution_contexts = tab.add_handler(
        "Runtime.executionContextCreated",
        stored_event_handler(Arc::clone(&execution_contexts)),
    );
    let _console_events = tab.add_handler(
        "Runtime.consoleAPICalled",
        stored_event_handler(Arc::clone(&console_events)),
    );
    let _exception_events = tab.add_handler(
        "Runtime.exceptionThrown",
        stored_event_handler(Arc::clone(&exception_events)),
    );
    let _binding_events = tab.add_handler(
        "Runtime.bindingCalled",
        stored_event_handler(Arc::clone(&binding_events)),
    );
    let _response_events = tab.add_handler(
        "Network.responseReceived",
        stored_event_handler(Arc::clone(&response_events)),
    );
    let _request_events = tab.add_handler(
        "Network.requestWillBeSent",
        stored_event_handler(Arc::clone(&request_events)),
    );
    let _failed_requests = tab.add_handler(
        "Network.loadingFailed",
        stored_event_handler(Arc::clone(&failed_requests)),
    );

    ensure(
        tab.add_preload_script("function(){window.__campaignDocumentStart='document-start';}")
            .await?,
        "document-start script was not registered",
    )?;
    tab.command("Page.enable", empty_object()).await?;
    tab.command("Runtime.enable", empty_object()).await?;
    tab.command("Network.enable", empty_object()).await?;
    tab.command(
        "Runtime.addBinding",
        object_value(&[("name", string_value("campaignBinding"))]),
    )
    .await?;
    eprintln!("core: CDP domains enabled; navigating identity target");
    tab.navigate(&server.url("/identity"), "complete", Duration::from_secs(20))
        .await
        .map_err(|error| failure(format!("navigate {}: {error}", server.url("/identity"))))?;

    let identity = evaluate_identity(&tab).await?;
    let user_agent = verify_identity_surfaces(&identity, &server.requests())?;
    ensure(
        field_text(field(&identity, "frame").ok_or("frame identity was omitted")?, "messageOrigin")
            == Some("null"),
        "identity frame did not have an opaque cross-origin origin",
    )?;
    ensure(
        field(&identity, "main").is_some(),
        "first navigation did not report the main frame identity",
    )?;
    ensure(
        object_field(&identity, "dedicatedWorker").is_some()
            && object_field(&identity, "sharedWorker").is_some()
            && object_field(&identity, "serviceWorker").is_some()
            && object_field(&identity, "frame").is_some(),
        "first navigation did not report every worker and frame identity",
    )?;
    ensure(
        evaluate_text(&tab, "window.__campaignDocumentStartObserved").await? == "document-start",
        "document-start hook did not run before the page's first inline script",
    )?;

    tab.evaluate("console.info('campaign-console-marker')")
        .await?;
    tab.evaluate("setTimeout(()=>{throw new Error('campaign-exception-marker')},0)")
        .await?;
    tab.evaluate("window.campaignBinding('campaign-binding-marker')")
        .await?;
    wait_for_event(&console_events, "campaign-console-marker").await?;
    wait_for_event(&exception_events, "campaign-exception-marker").await?;
    wait_for_event(&binding_events, "campaign-binding-marker").await?;
    wait_for_event(&execution_contexts, &server.base_url()).await?;

    let response_text = evaluate_text(
        &tab,
        "(async()=>await fetch('/body').then(response=>response.text()))()",
    )
    .await?;
    ensure(response_text == "campaign-response-body", "local response body mismatch")?;
    let response_id = response_request_id(&response_events, "/body")
        .ok_or("CDP did not report the local response request id")?;
    let response = tab
        .command(
            "Network.getResponseBody",
            object_value(&[("requestId", string_value(&response_id))]),
        )
        .await?;
    ensure(
        field_text(&response, "body") == Some("campaign-response-body"),
        "CDP Network.getResponseBody returned the wrong local body",
    )?;

    let screenshot = tab
        .command(
            "Page.captureScreenshot",
            object_value(&[("format", string_value("png"))]),
        )
        .await?;
    ensure(
        field_text(&screenshot, "data").is_some_and(|data| data.starts_with("iVBOR")),
        "CDP screenshot did not return a PNG image",
    )?;
    let pdf = tab.command("Page.printToPDF", empty_object()).await?;
    ensure(
        field_text(&pdf, "data").is_some_and(|data| data.starts_with("JVBERi0")),
        "CDP PDF rendering did not return a PDF document",
    )?;

    tab.block_requests(vec![BlockPattern {
        protocol: "http".to_owned(),
        hostname: format!("127.0.0.1:{}", server.port()),
        pathname: "/intercepted.js".to_owned(),
    }])
    .await?;
    let intercepted = tab
        .evaluate("(async()=>{try{await fetch('/intercepted.js');return false}catch{return true}})()")
        .await?;
    ensure(
        matches!(intercepted, Value::Bool(true)),
        "Shadowdriver request interception did not block the local request",
    )?;
    async_io::Timer::after(Duration::from_millis(150)).await;
    ensure(
        !server
            .requests()
            .iter()
            .any(|record| record.request.target.starts_with("/intercepted.js")),
        "intercepted request reached the local server",
    )?;

    let ublock_script = evaluate_text(
        &tab,
        "(async()=>{const script=document.createElement('script');script.src='/ads_controller.js';return await new Promise(resolve=>{script.onload=()=>resolve('loaded');script.onerror=()=>resolve('failed');document.head.appendChild(script)})})()",
    )
    .await?;
    async_io::Timer::after(Duration::from_millis(150)).await;
    ensure(
        ublock_script == "failed"
            && event_snapshot(&failed_requests)
                .iter()
                .any(|event| event.contains("ERR_BLOCKED_BY_CLIENT"))
            && !server
                .requests()
                .iter()
                .any(|record| record.request.target.starts_with("/ads_controller.js")),
        format!(
            "uBlock did not block its local EasyList fixture: result={ublock_script}, requests={:?}, failures={:?}",
            server.requests(),
            event_snapshot(&failed_requests)
        ),
    )?;

    tab.command("Page.reload", empty_object()).await?;
    tab.ready(Duration::from_secs(20)).await?;
    let reloaded = evaluate_identity(&tab).await?;
    let reloaded_user_agent = verify_identity_surfaces(&reloaded, &server.requests())?;
    ensure(
        reloaded_user_agent == user_agent,
        "BrowserNative identity changed after reload",
    )?;

    let target = session.new_tab(TabOptions::default()).await?;
    target
        .navigate(&server.url("/page?index=new-target"), "complete", Duration::from_secs(15))
        .await?;
    let target_identity = evaluate_json(
        &target,
        "JSON.stringify({userAgent:navigator.userAgent,platform:navigator.platform,uaCH:navigator.userAgentData?.platform})",
    )
    .await?;
    ensure(
        field_text(&target_identity, "userAgent") == Some(user_agent.as_str()),
        "BrowserNative identity changed on a newly created target",
    )?;
    target.close().await?;

    let gpu = session.command("SystemInfo.getInfo", empty_object()).await?;
    verify_metal_gpu(&gpu, "headless")?;
    println!(
        "browser identity surfaces passed: userAgent={user_agent} requestHeaders=User-Agent,Sec-CH-UA,Sec-CH-UA-Platform reload=true frameOrigin=opaque-data-url workerTypes=dedicated,shared,service"
    );
    Ok(())
}

async fn run_page_wave(
    session: &CrawlerSession,
    expected_user_agent: &str,
    count: usize,
) -> Result<(), Box<dyn Error>> {
    let mut completed = 0;
    for first in (0..count).step_by(PAGE_ADMISSION) {
        let end = (first + PAGE_ADMISSION).min(count);
        let mut pages = Vec::with_capacity(end - first);
        for index in first..end {
            let session = session.clone();
            let url = format!(
                "data:text/html,<title>shadowdriver-{index}</title><body>shadowdriver-{index}</body>"
            );
            let expected_user_agent = expected_user_agent.to_owned();
            pages.push(async move {
                let tab = session.new_tab(TabOptions::default()).await?;
                tab.navigate(&url, "complete", Duration::from_secs(15))
                    .await
                    .map_err(|error| failure(format!("page {index} navigation failed: {error}")))?;
                let value = evaluate_json(
                    &tab,
                    "JSON.stringify({title:document.title,body:document.body.textContent,userAgent:navigator.userAgent})",
                )
                .await?;
                ensure(
                    field_text(&value, "title") == Some(format!("shadowdriver-{index}").as_str())
                        && field_text(&value, "body") == Some(format!("shadowdriver-{index}").as_str())
                        && field_text(&value, "userAgent") == Some(expected_user_agent.as_str()),
                    format!("page {index} lost content or received another page's identity"),
                )?;
                let screenshot = tab
                    .command(
                        "Page.captureScreenshot",
                        object_value(&[("format", string_value("png"))]),
                    )
                    .await?;
                ensure(
                    field_text(&screenshot, "data").is_some_and(|data| data.starts_with("iVBOR")),
                    format!("page {index} capture was empty"),
                )?;
                tab.close().await?;
                Ok::<(), Box<dyn Error>>(())
            });
        }
        try_join_all(pages).await?;
        completed += end - first;
    }
    ensure(completed == count, format!("only {completed} of {count} pages completed"))?;
    println!("page wave passed: pages={count} admission={PAGE_ADMISSION}");
    Ok(())
}

async fn run_extension_acceptance(
    binary: &PathBuf,
    profile_base: &PathBuf,
    server: &LocalHttpServer,
    proxy_url: &str,
    manifest_version: u8,
) -> Result<(), Box<dyn Error>> {
    let name = format!("mv{manifest_version}");
    let parent = profile_base
        .parent()
        .ok_or("browser profile has no parent directory")?;
    let extension = parent.join("campaign-extensions").join(&name);
    let profile = parent.join(format!("acceptance-{name}"));
    fs::create_dir_all(&extension)?;
    let background = if manifest_version == 3 {
        ",\"background\":{\"service_worker\":\"background.js\"},\"permissions\":[\"storage\"]"
    } else {
        ",\"background\":{\"scripts\":[\"background.js\"]},\"permissions\":[\"storage\"]"
    };
    let manifest = format!(
        "{{\"manifest_version\":{manifest_version},\"name\":\"Shadowdriver {name}\",\"version\":\"1.0\"{background},\"content_scripts\":[{{\"matches\":[\"http://*/*\"],\"js\":[\"content.js\"],\"run_at\":\"document_start\"}}]}}"
    );
    fs::write(extension.join("manifest.json"), manifest)?;
    fs::write(
        extension.join("background.js"),
        "chrome.runtime.onInstalled.addListener(()=>chrome.storage.local.set({campaignStarted:true}));",
    )?;
    fs::write(
        extension.join("content.js"),
        format!(
            "document.documentElement.dataset.campaignExtension=\"{name}\";"
        ),
    )?;

    let mut options = ChromiumSessionOptions::new(
        binary.clone(),
        profile.clone(),
    );
    options.headless = true;
    options.persistent = false;
    options.identity_mode = BrowserIdentityMode::BrowserNative;
    options.stealth = true;
    options.extensions = vec![extension.clone()];
    isolate_profile_network(&profile, proxy_url)?;
    let session = match CrawlerSession::ensure_chromium(options).await {
        Ok(session) => session,
        Err(error) => {
            let _ = fs::remove_dir_all(&extension);
            return Err(error.into());
        }
    };
    let result = async {
        let provenance = session.provenance().await?;
        ensure(
            provenance.extensions.len() == 1
                && provenance.identity_mode == BrowserIdentityMode::BrowserNative,
            format!("Chromium did not retain the MV{manifest_version} BrowserNative extension policy"),
        )?;
        let tab = session.new_tab(TabOptions::default()).await?;
        tab.navigate(&server.url("/extension"), "complete", Duration::from_secs(15))
            .await?;
        let marker = evaluate_text(&tab, "document.documentElement.dataset.campaignExtension")
            .await?;
        ensure(
            marker == name,
            format!("MV{manifest_version} content script did not run; marker={marker:?}"),
        )?;
        println!("extension acceptance passed: manifest_version={manifest_version}");
        Ok::<(), Box<dyn Error>>(())
    }
    .await;
    session.close(true).await;
    let _ = fs::remove_dir_all(&extension);
    result
}

async fn run_headed_acceptance(
    binary: &PathBuf,
    profile_base: &PathBuf,
    server: &LocalHttpServer,
    proxy_url: &str,
) -> Result<(), Box<dyn Error>> {
    let parent = profile_base
        .parent()
        .ok_or("browser profile has no parent directory")?;
    let profile = parent.join("acceptance-headed");
    let mut options = ChromiumSessionOptions::new(binary.clone(), profile.clone());
    options.headless = false;
    options.persistent = false;
    options.identity_mode = BrowserIdentityMode::BrowserNative;
    options.stealth = true;
    isolate_profile_network(&profile, proxy_url)?;
    let session = CrawlerSession::ensure_chromium(options).await?;
    let result = async {
        let provenance = session.provenance().await?;
        ensure(!provenance.headless, "headed launch was recorded as headless")?;
        let tab = session.new_tab(TabOptions::default()).await?;
        tab.navigate(&server.url("/page?index=headed"), "complete", Duration::from_secs(15))
            .await?;
        let title = evaluate_text(&tab, "document.title").await?;
        ensure(title == "shadowdriver-headed", "headed Chromium did not render the local page")?;
        let gpu = session.command("SystemInfo.getInfo", empty_object()).await?;
        verify_metal_gpu(&gpu, "headed")?;
        tab.close().await?;
        Ok::<(), Box<dyn Error>>(())
    }
    .await;
    session.close(true).await;
    result?;
    println!("headed app acceptance passed: visible browser window launched and rendered a local page");
    Ok(())
}

fn fixture_servers() -> (LocalHttpServer, String) {
    let frame_page = r#"<!doctype html><script>
const identity=async()=>{const data=navigator.userAgentData;return {userAgent:navigator.userAgent,platform:navigator.platform,uaCH:data?{brands:data.brands,mobile:data.mobile,platform:data.platform,high:await data.getHighEntropyValues(["architecture","bitness","model","platformVersion","uaFullVersion"])}:null}};
window.onerror=message=>parent.postMessage({kind:"frame-script-error",error:String(message)},"*");
identity().then(value=>parent.postMessage({kind:"frame-identity",identity:value},"*"),error=>parent.postMessage({kind:"frame-identity-error",error:String(error)},"*"));
</script><body>cross-origin frame</body>"#;
    let frame_url = data_url(frame_page);
    let page = r#"<!doctype html><html><head><meta charset="utf-8"><title>identity</title></head><body>BrowserNative identity fixture<script>
window.__campaignDocumentStartObserved=window.__campaignDocumentStart;
window.__campaignIdentityResults={};
const observe=(name,promise)=>promise.then(value=>(window.__campaignIdentityResults[name]="complete",value),error=>(window.__campaignIdentityResults[name]=String(error),Promise.reject(error)));
const identity=async()=>{const data=navigator.userAgentData;return {userAgent:navigator.userAgent,platform:navigator.platform,uaCH:data?{brands:data.brands,mobile:data.mobile,platform:data.platform,high:await data.getHighEntropyValues(["architecture","bitness","model","platformVersion","uaFullVersion"])}:null}};
const main=observe("main",identity());
window.__campaignFrameMessages=[];
const frameIdentity=observe("frame",new Promise(resolve=>window.addEventListener("message",event=>{window.__campaignFrameMessages.push({origin:event.origin,data:event.data});if(event.data?.kind==="frame-identity")resolve({...event.data.identity,messageOrigin:event.origin});if(event.data?.kind==="frame-identity-error")resolve({error:event.data.error})})));
const dedicated=observe("dedicatedWorker",new Promise(resolve=>{const worker=new Worker("/worker.js");worker.onmessage=event=>resolve(event.data);worker.onerror=event=>resolve({error:event.message});worker.postMessage("identity")}));
const shared=observe("sharedWorker",new Promise(resolve=>{const worker=new SharedWorker("/shared-worker.js");worker.port.onmessage=event=>resolve(event.data);worker.onerror=event=>resolve({error:event.message});worker.port.start();worker.port.postMessage("identity")}));
const service=observe("serviceWorker",new Promise(async resolve=>{await navigator.serviceWorker.register("/service-worker.js");const registration=await navigator.serviceWorker.ready;const worker=registration.active;const channel=new MessageChannel();channel.port1.onmessage=event=>resolve(event.data);worker.postMessage("identity",[channel.port2])}));
const frame=document.createElement("iframe");frame.onload=()=>window.__campaignFrameLoaded=true;frame.onerror=()=>window.__campaignFrameError=true;frame.src="$FRAME_URL";document.body.appendChild(frame);
window.__identityPromise=Promise.all([main,frameIdentity,dedicated,shared,service]).then(([main,frame,dedicatedWorker,sharedWorker,serviceWorker])=>({main,frame,dedicatedWorker,sharedWorker,serviceWorker}));
</script></body></html>"#
        .replace("$FRAME_URL", &frame_url);
    let worker = r#"const identity=async()=>{const data=navigator.userAgentData;return {userAgent:navigator.userAgent,platform:navigator.platform,uaCH:data?{brands:data.brands,mobile:data.mobile,platform:data.platform,high:await data.getHighEntropyValues(["architecture","bitness","model","platformVersion","uaFullVersion"])}:null}};self.onmessage=async()=>{try{self.postMessage(await identity())}catch(error){self.postMessage({error:String(error)})}};"#;
    let shared_worker = r#"const identity=async()=>{const data=navigator.userAgentData;return {userAgent:navigator.userAgent,platform:navigator.platform,uaCH:data?{brands:data.brands,mobile:data.mobile,platform:data.platform,high:await data.getHighEntropyValues(["architecture","bitness","model","platformVersion","uaFullVersion"])}:null}};onconnect=event=>{const port=event.ports[0];port.onmessage=async()=>{try{port.postMessage(await identity())}catch(error){port.postMessage({error:String(error)})}};port.start()};"#;
    let service_worker = r#"const identity=async()=>{const data=navigator.userAgentData;return {userAgent:navigator.userAgent,platform:navigator.platform,uaCH:data?{brands:data.brands,mobile:data.mobile,platform:data.platform,high:await data.getHighEntropyValues(["architecture","bitness","model","platformVersion","uaFullVersion"])}:null}};self.addEventListener("message",async event=>{try{event.ports[0].postMessage(await identity())}catch(error){event.ports[0].postMessage({error:String(error)})}});"#;
    let server = LocalHttpServer::bind_handler(move |request: &Request| {
        let path = request.target.split('?').next().unwrap_or_default();
        match path {
            "/identity" => html_response(page.clone()),
            "/worker.js" => Response::ok("application/javascript", worker.as_bytes().to_vec()),
            "/shared-worker.js" => {
                Response::ok("application/javascript", shared_worker.as_bytes().to_vec())
            }
            "/service-worker.js" => {
                Response::ok("application/javascript", service_worker.as_bytes().to_vec())
            }
            "/body" => Response::ok("text/plain; charset=utf-8", b"campaign-response-body".to_vec()),
            "/ads_controller.js" => Response::ok(
                "application/javascript",
                b"window.__campaignBlockedScriptLoaded=true;".to_vec(),
            ),
            "/intercepted.js" => Response::ok(
                "application/javascript",
                b"window.__campaignInterceptedScriptLoaded=true;".to_vec(),
            ),
            "/extension" => html_response(
                "<!doctype html><title>extension</title><body>extension fixture</body>".to_owned(),
            ),
            "/page" => {
                let index = request
                    .target
                    .split("index=")
                    .nth(1)
                    .unwrap_or("unknown")
                    .to_owned();
                html_response(format!(
                    "<!doctype html><title>shadowdriver-{index}</title><body>shadowdriver-{index}</body>"
                ))
            }
            _ => Response::not_found(),
        }
    });
    (server, frame_url)
}

fn data_url(document: &str) -> String {
    let mut url = String::from("data:text/html,");
    for byte in document.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            url.push(char::from(byte));
        } else {
            url.push_str(&format!("%{byte:02X}"));
        }
    }
    url
}

fn isolate_profile_network(profile: &PathBuf, proxy_url: &str) -> Result<(), Box<dyn Error>> {
    let preferences = profile.join("Default").join("Preferences");
    fs::create_dir_all(
        preferences
            .parent()
            .ok_or("browser preferences path has no parent")?,
    )?;
    let proxy = object_value(&[
        ("mode", string_value("fixed_servers")),
        ("server", string_value(proxy_url)),
        ("bypass_list", string_value("localhost;127.0.0.1;[::1]")),
    ]);
    fs::write(
        preferences,
        serialize(&object_value(&[("proxy", proxy)])),
    )?;
    Ok(())
}

fn html_response(body: String) -> Response {
    Response::ok("text/html; charset=utf-8", body.into_bytes())
        .with_header("Accept-CH", "Sec-CH-UA-Platform, Sec-CH-UA-Platform-Version")
}

fn verify_identity_surfaces(
    identity: &Value,
    requests: &[http::RequestRecord],
) -> Result<String, Box<dyn Error>> {
    let main = field(identity, "main").ok_or("identity result omitted the main frame")?;
    let user_agent = field_text(main, "userAgent").ok_or("main frame omitted userAgent")?;
    let platform = field_text(main, "platform").ok_or("main frame omitted platform")?;
    let ua_ch = field(main, "uaCH").ok_or("main frame omitted userAgentData")?;
    ensure(!matches!(ua_ch, Value::Null), "Chromium omitted User-Agent Client Hints")?;
    for name in ["frame", "dedicatedWorker", "sharedWorker", "serviceWorker"] {
        let context = field(identity, name).ok_or_else(|| format!("identity result omitted {name}"))?;
        ensure(
            field_text(context, "error").is_none(),
            format!("BrowserNative identity failed in {name}: {}", serialize(context)),
        )?;
        ensure(
            field_text(context, "userAgent") == Some(user_agent),
            format!(
                "BrowserNative userAgent changed in {name}: expected={user_agent:?} context={}",
                serialize(context)
            ),
        )?;
        ensure(
            field_text(context, "platform") == Some(platform),
            format!(
                "BrowserNative platform changed in {name}: expected={platform:?} context={}",
                serialize(context)
            ),
        )?;
        if name == "frame" && field_text(context, "messageOrigin") == Some("null") {
            ensure(
                matches!(field(context, "uaCH"), Some(Value::Null)),
                format!("opaque frame unexpectedly exposed UA-CH: {}", serialize(context)),
            )?;
        } else {
            ensure(
                field(context, "uaCH").is_some_and(|value| serialize(value) == serialize(ua_ch)),
                format!(
                    "BrowserNative UA-CH changed in {name}: expected={} context={}",
                    serialize(ua_ch),
                    serialize(context)
                ),
            )?;
        }
    }
    let request = requests
        .iter()
        .rev()
        .find(|record| record.request.target == "/identity")
        .ok_or("the local identity navigation was not recorded")?;
    ensure(
        request_header(request, "user-agent") == Some(user_agent),
        "native JavaScript userAgent did not match the request header",
    )?;
    let client_platform = request_header(request, "sec-ch-ua-platform")
        .ok_or("request did not contain the User-Agent Client Hints platform header")?;
    ensure(
        client_platform.trim_matches('"') == field_text(ua_ch, "platform").unwrap_or_default(),
        "UA-CH platform header did not match navigator.userAgentData.platform",
    )?;
    let brands = request_header(request, "sec-ch-ua")
        .ok_or("request did not contain the Sec-CH-UA header")?;
    ensure(
        brands.contains("Chromium") || brands.contains("Google Chrome"),
        "Sec-CH-UA did not contain the browser brand reported by Chromium",
    )?;
    Ok(user_agent.to_owned())
}

fn verify_metal_gpu(gpu: &Value, launch_mode: &str) -> Result<(), Box<dyn Error>> {
    let gpu_info = field(gpu, "gpu").ok_or("GPU report omitted GPU information")?;
    let attributes = field(gpu_info, "auxAttributes").ok_or("GPU report omitted auxiliary attributes")?;
    let implementation = field_text(attributes, "glImplementationParts")
        .ok_or("GPU report omitted the GL implementation")?;
    let renderer = field_text(attributes, "glRenderer").ok_or("GPU report omitted the GL renderer")?;
    let features = field(gpu_info, "featureStatus").ok_or("GPU report omitted feature status")?;
    let compositing = field_text(features, "gpu_compositing").unwrap_or("unreported");
    let sandboxed = matches!(field(attributes, "sandboxed"), Some(Value::Bool(true)));
    ensure(
        implementation.contains("angle=metal")
            && renderer.contains("ANGLE Metal Renderer")
            && compositing == "enabled"
            && sandboxed,
        format!(
            "{launch_mode} Chromium did not report sandboxed Metal acceleration: backend={implementation}, renderer={renderer}, gpu_compositing={compositing}, sandboxed={sandboxed}"
        ),
    )?;
    println!(
        "{launch_mode} Metal acceptance passed: backend={implementation} renderer={renderer} gpuCompositing={compositing} sandboxed={sandboxed}"
    );
    Ok(())
}

fn response_request_id(events: &Arc<Mutex<Vec<String>>>, path: &str) -> Option<String> {
    event_snapshot(events).iter().find_map(|event| {
        let value = json::from_str::<Value>(event).ok()?;
        let response = field(&value, "response")?;
        let url = field_text(response, "url")?;
        if !url.contains(path) {
            return None;
        }
        field_text(&value, "requestId").map(str::to_owned)
    })
}

fn stored_event_handler(events: Arc<Mutex<Vec<String>>>) -> EventHandler {
    Box::new(move |event| {
        events
            .lock()
            .expect("lock acceptance events")
            .push(serialize(&event));
    })
}

async fn wait_for_event(events: &Arc<Mutex<Vec<String>>>, marker: &str) -> Result<(), Box<dyn Error>> {
    for _ in 0..100 {
        if event_snapshot(events).iter().any(|event| event.contains(marker)) {
            return Ok(());
        }
        async_io::Timer::after(Duration::from_millis(20)).await;
    }
    Err(failure(format!("CDP did not report event marker {marker:?}")))
}

fn event_snapshot(events: &Arc<Mutex<Vec<String>>>) -> Vec<String> {
    events.lock().expect("lock acceptance events").clone()
}

async fn evaluate_json(tab: &CrawlerTab, expression: &str) -> Result<Value, Box<dyn Error>> {
    let text = evaluate_text(tab, expression).await?;
    Ok(json::from_str::<Value>(&text)?)
}

async fn evaluate_identity(tab: &CrawlerTab) -> Result<Value, Box<dyn Error>> {
    let result = evaluate_json(
        tab,
        "(async()=>JSON.stringify(await Promise.race([window.__identityPromise.then(value=>({status:'complete',value}),error=>({status:'error',error:String(error),results:window.__campaignIdentityResults})),new Promise(resolve=>setTimeout(()=>resolve({status:'timeout',results:window.__campaignIdentityResults,frameLoaded:window.__campaignFrameLoaded,frameError:window.__campaignFrameError,frameMessages:window.__campaignFrameMessages}),10000))])))()",
    )
    .await?;
    ensure(
        field_text(&result, "status") == Some("complete"),
        format!("identity surfaces did not settle: {}", serialize(&result)),
    )?;
    field(&result, "value")
        .cloned()
        .ok_or_else(|| failure("identity evaluation completed without a value"))
}

async fn evaluate_text(tab: &CrawlerTab, expression: &str) -> Result<String, Box<dyn Error>> {
    match tab.evaluate(expression).await? {
        Value::String(value) => Ok(value),
        other => Err(failure(format!("JavaScript returned a non-string value: {other:?}"))),
    }
}

fn field<'a>(value: &'a Value, name: &str) -> Option<&'a Value> {
    match value {
        Value::Object(object) => object.get(name),
        _ => None,
    }
}

fn field_text<'a>(value: &'a Value, name: &str) -> Option<&'a str> {
    match field(value, name)? {
        Value::String(text) => Some(text),
        _ => None,
    }
}

fn object_field<'a>(value: &'a Value, name: &str) -> Option<&'a Value> {
    match field(value, name)? {
        Value::Object(_) => Some(field(value, name)?),
        _ => None,
    }
}

fn request_header<'a>(record: &'a http::RequestRecord, name: &str) -> Option<&'a str> {
    record
        .request
        .headers
        .iter()
        .find(|(key, _)| key.eq_ignore_ascii_case(name))
        .map(|(_, value)| value.as_str())
}

fn string_value(value: &str) -> Value {
    Value::String(value.to_owned())
}

fn object_value(values: &[(&str, Value)]) -> Value {
    let mut object = Object::new();
    for (key, value) in values {
        object.insert((*key).to_owned(), value.clone());
    }
    Value::Object(object)
}

fn empty_object() -> Value {
    Value::Object(Object::new())
}

fn serialize(value: &Value) -> String {
    json::to_string(value)
}

fn ensure(condition: bool, message: impl Into<String>) -> Result<(), Box<dyn Error>> {
    if condition {
        Ok(())
    } else {
        Err(failure(message))
    }
}

fn failure(message: impl Into<String>) -> Box<dyn Error> {
    std::io::Error::other(message.into()).into()
}

async fn try_join_all<F, T, E>(futures: Vec<F>) -> Result<Vec<T>, E>
where
    F: Future<Output = Result<T, E>>,
{
    let mut pending: Vec<Option<Pin<Box<F>>>> = futures
        .into_iter()
        .map(|future| Some(Box::pin(future)))
        .collect();
    let mut results: Vec<Option<T>> = (0..pending.len()).map(|_| None).collect();

    futures_lite::future::poll_fn(move |context| {
        for (slot, result) in pending.iter_mut().zip(results.iter_mut()) {
            if result.is_some() {
                continue;
            }
            let Some(future) = slot.as_mut() else {
                continue;
            };
            match future.as_mut().poll(context) {
                Poll::Pending => {}
                Poll::Ready(Ok(value)) => {
                    *result = Some(value);
                    *slot = None;
                }
                Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
            }
        }

        if results.iter().all(Option::is_some) {
            let values = std::mem::take(&mut results)
                .into_iter()
                .map(|value| value.expect("ready result"))
                .collect();
            Poll::Ready(Ok(values))
        } else {
            Poll::Pending
        }
    })
    .await
}
