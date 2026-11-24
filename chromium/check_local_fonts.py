"""Exercise Moltskin's native Local Font Access switch in two visible browsers."""

import argparse
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import sync_playwright


POLICY_SWITCH = "--moltskin-disable-local-font-access"
PAGE = b"""<!doctype html><meta charset="utf-8"><title>Local Font Access test</title>
<button>Count local fonts</button> <output></output>
<script>
document.querySelector('button').onclick = async () => {
  const output = document.querySelector('output');
  try {
    const fonts = await window.queryLocalFonts();
    output.textContent = Array.isArray(fonts) ? String(fonts.length) : 'Invalid response';
  } catch (error) {
    output.textContent = error.name;
  }
};
</script>"""


class TestPage(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_):
        pass


def check_page(context, origin, name, restricted, restarted):
    page = context.pages[0]
    page.goto(origin)
    page.bring_to_front()
    page.wait_for_function("document.visibilityState === 'visible'")
    # Apply the same site grant in both browsers. A restricted profile must
    # still return [] after the user has granted permission on this origin.
    context.grant_permissions(["local-fonts"], origin=origin)
    grant = page.evaluate("async () => (await navigator.permissions.query({name:'local-fonts'})).state")
    if grant != "granted":
        raise RuntimeError(f"Profile {name}: failed to establish test site grant ({grant})")

    marker = page.evaluate("localStorage.getItem('profile')")
    if marker != (name if restarted else None):
        raise RuntimeError(f"Profile {name}: unexpected storage marker {marker!r}")
    page.evaluate("name => localStorage.setItem('profile', name)", name)
    page.get_by_role("button").click()
    page.wait_for_function(
        "document.querySelector('output').textContent !== ''", polling=100, timeout=60_000
    )
    value = page.locator("output").text_content()
    policy = "restricted" if restricted else "standard"
    print(f"{name}: {policy}, site grant={grant}, result={value}", flush=True)
    if not value.isdecimal() or (int(value) == 0) != restricted:
        expected = "an empty list" if restricted else "at least one font"
        raise RuntimeError(f"Profile {name} ({policy}) expected {expected}, got {value!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path, help="Linux chrome or macOS Chromium.app/Contents/MacOS/Chromium")
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    with ThreadingHTTPServer(("127.0.0.1", 0), TestPage) as server, TemporaryDirectory() as tmp:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                # Swap policies on restart, reusing the exact same directories.
                for restarted in (False, True):
                    print("Restart with swapped policies" if restarted else "First launch", flush=True)
                    with ExitStack() as stack:
                        for name, restricted in (("A", restarted), ("B", not restarted)):
                            context = playwright.chromium.launch_persistent_context(
                                str(Path(tmp) / name), executable_path=str(executable), headless=False,
                                args=[POLICY_SWITCH] if restricted else [],
                            )
                            stack.callback(context.close)
                            check_page(context, origin, name, restricted, restarted)
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    main()
