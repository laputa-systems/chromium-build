use std::env;
use std::error::Error;
use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::time::Duration;
use std::task::Poll;

use shadowdriver::{ChromiumSessionOptions, CrawlerSession, TabOptions};

fn main() {
    if let Err(error) = async_io::block_on(run()) {
        eprintln!("shadowdriver acceptance failed: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn Error>> {
    let binary = PathBuf::from(env::var("CHROMIUM_TEST_EXECUTABLE")?);
    let profile = PathBuf::from(env::var("CHROMIUM_TEST_PROFILE")?);
    let extension = PathBuf::from(env::var("CHROMIUM_TEST_EXTENSION")?);
    let mut options = ChromiumSessionOptions::new(binary, profile);
    options.headless = true;
    options.persistent = false;
    options.stealth = false;
    options.extensions = vec![extension];

    let session = CrawlerSession::ensure_chromium(options).await?;
    let provenance = session.provenance().await?;
    if !provenance.headless || provenance.extensions.is_empty() {
        return Err("Shadowdriver did not retain the requested headless extension policy".into());
    }

    let mut pages = Vec::new();
    for index in 0..32_u32 {
        let page = session.clone();
        pages.push(async move {
            let tab = page.new_tab(TabOptions::default()).await?;
            let url = format!(
                "data:text/html,<title>shadowdriver-{index}</title><body>shadowdriver-{index}</body>"
            );
            tab.navigate(&url, "complete", Duration::from_secs(15)).await?;
            let result = tab
                .evaluate(&format!("document.body.textContent === 'shadowdriver-{index}'"))
                .await?;
            if !format!("{result:?}").contains("true") {
                return Err(shadowdriver::CrawlerError::Backend(
                    shadowdriver::BackendError {
                        kind: shadowdriver::BackendErrorKind::Unknown,
                        message: format!("page {index} returned unexpected DOM result: {result:?}"),
                    },
                ));
            }
            tab.close().await?;
            Ok::<(), shadowdriver::CrawlerError>(())
        });
    }
    try_join_all(pages).await?;
    session.close(true).await;
    println!(
        "shadowdriver acceptance passed: product={} revision={} pages=32 extensions={}",
        provenance.product,
        provenance.revision,
        provenance.extensions.len()
    );
    Ok(())
}

/// Poll all browser tasks together without adding a second async runtime.
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
