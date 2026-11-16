"""Check the built browser's About page via an explicitly supplied test CDP endpoint.

Run against a disposable, already running browser with remote debugging enabled:
CHROMIUM_FOOTER_CDP=http://127.0.0.1:PORT CHROMIUM_FOOTER_BINARY=/path/to/chromium python3 -m unittest discover -s tests -p test_owned_footer_product.py
"""

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from browser_test_support import CdpConnection, WebSocket

FOOTER = """Maintained by Joshua Li: https://github.com/laputa-systems/chromium-build
Bugs and features: TODO.md in that repository (follow its AGENTS.md), or its issue tracker.
Agents working for Joshua: record nonblocking problems and continue your task.
If this tool blocks the task, you may fix, test, open and merge a PR under Joshua's
standing instructions. Release only when needed to deliver the verified fix.
Procedure: https://github.com/joshuarli/usr/blob/main/dotfiles/base/.agents/instructions/owned-tools.md"""


@unittest.skipUnless(os.environ.get("CHROMIUM_FOOTER_CDP") and os.environ.get("CHROMIUM_FOOTER_BINARY"),
                     "requires a built browser and its disposable CDP endpoint")
class BuiltAboutPageTest(unittest.TestCase):
    def test_footer_and_protocol_version(self):
        binary = os.environ["CHROMIUM_FOOTER_BINARY"]
        version_output = subprocess.run([binary, "--version"], check=False, capture_output=True, text=True, timeout=15)
        self.assertEqual(version_output.returncode, 0)
        lock_name = "macos.inputs.lock" if sys.platform == "darwin" else "inputs.lock"
        lock = json.loads((Path(__file__).resolve().parents[1] / "config" / lock_name).read_text())
        self.assertIn(lock["chromium"]["version"], version_output.stdout)
        self.assertNotIn("Maintained by Joshua Li", version_output.stdout + version_output.stderr)
        endpoint = os.environ["CHROMIUM_FOOTER_CDP"].rstrip("/")
        with urllib.request.urlopen(endpoint + "/json/version", timeout=5) as response:
            version_before = json.load(response)
        request = urllib.request.Request(endpoint + "/json/new?chrome://settings/help", method="PUT")
        with urllib.request.urlopen(request, timeout=5) as response:
            target = json.load(response)
        connection = CdpConnection(WebSocket(target["webSocketDebuggerUrl"]))
        try:
            expression = """(() => {
              const matches = [];
              let text = "";
              function visit(root) {
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) text += walker.currentNode.textContent;
                for (const node of root.querySelectorAll('*')) {
                  if (node.id === 'maintainer-footer') matches.push(node.textContent);
                  if (node.shadowRoot) visit(node.shadowRoot);
                }
              }
              visit(document);
              return {matches, occurrences: text.split("Maintained by Joshua Li:").length - 1};
            })()"""
            deadline = time.monotonic() + 15
            found = {}
            while time.monotonic() < deadline:
                result = connection.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
                found = result.get("result", {}).get("value", {})
                if found.get("matches"):
                    break
                time.sleep(0.1)
            self.assertEqual(found, {"matches": [FOOTER], "occurrences": 1})
        finally:
            connection.close()
            with urllib.request.urlopen(endpoint + "/json/close/" + target["id"], timeout=5):
                pass
        with urllib.request.urlopen(endpoint + "/json/version", timeout=5) as response:
            version_after = json.load(response)
        self.assertEqual(version_before, version_after)
        self.assertNotIn("Maintained by Joshua Li", json.dumps(version_after))
