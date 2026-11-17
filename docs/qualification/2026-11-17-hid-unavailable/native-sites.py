import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from browser_test_support import CdpConnection, WebSocket, record_process_group, terminate_process
from helium_test_fast import wait_devtools_line, navigate, evaluate, close_browser, sandbox_observations

p = argparse.ArgumentParser()
p.add_argument('--browser', required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--seconds', type=int, default=60)
p.add_argument('--site', choices=['instagram', 'x'])
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
results = []
for name, url in [('instagram', 'https://www.instagram.com/'), ('x', 'https://x.com/i/flow/login')]:
    if a.site and name != a.site:
        continue
    profile = tempfile.mkdtemp(prefix=name+'-', dir=a.output)
    result = {'site': name, 'url': url, 'samples': []}
    process = None
    connection = None
    browser = None
    try:
        with (a.output / (name+'.log')).open('w') as log:
            process = subprocess.Popen([a.browser, '--headless=new', '--disable-gpu', '--in-process-gpu', '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-component-extensions-with-background-pages', '--disable-background-networking', '--enable-features=NetworkServiceInProcess2', '--disable-features=helium-noise,helium-split-clienthello', '--user-data-dir='+profile, '--remote-debugging-port=0', 'about:blank'], stdout=log, stderr=log, start_new_session=True)
        group = os.getpgid(process.pid)
        record_process_group(process, group)
        endpoint = wait_devtools_line(a.output / (name+'.log'), process, 30)
        browser = CdpConnection(WebSocket(endpoint))
        from urllib.parse import urlparse
        port = urlparse(endpoint).port
        request = urllib.request.Request(f'http://127.0.0.1:{port}/json/new?about:blank', method='PUT')
        with urllib.request.urlopen(request, timeout=10) as response:
            target = json.load(response)
        connection = CdpConnection(WebSocket(target['webSocketDebuggerUrl']))
        navigate(connection, url)
        ready_deadline = time.monotonic()+30
        while True:
            initial = evaluate(connection, '({url:location.href,body:document.body.innerText.length})')
            if initial['url'].startswith('https://') and initial['body'] > 0:
                break
            if time.monotonic() >= ready_deadline:
                if initial['url'].startswith('chrome-error:'):
                    initial['error_page'] = evaluate(connection, 'document.body.innerText.slice(0,500)')
                raise RuntimeError('site did not render content: '+str(initial))
            time.sleep(1)
        deadline = time.monotonic()+a.seconds
        while True:
            assert process.poll() is None, process.returncode
            sample = evaluate(connection, '({title:document.title,url:location.href,state:document.readyState,body:document.body.innerText.length})')
            assert sample['url'].startswith('https://'), sample
            assert sample['body'] > 0, sample
            result['samples'].append(sample)
            if time.monotonic() >= deadline:
                break
            time.sleep(5)
        result['sandbox'] = sandbox_observations(browser, require_pid_namespace=True)
        connection.close()
        connection = None
        result['exit'] = close_browser(browser, process, group)
        process = None
        browser = None
        result['status'] = 'complete'
    except BaseException as error:
        result['status'] = 'failed'
        result['error'] = str(error)
    finally:
        if connection is not None:
            connection.close()
        if browser is not None:
            browser.close()
        if process is not None:
            result['cleanup'] = terminate_process(process)
    results.append(result)
    (a.output/'sites.json').write_text(json.dumps(results, indent=2)+'\n')
    print(name, result['status'], result.get('error', ''), flush=True)
assert all(r['status'] == 'complete' for r in results)
