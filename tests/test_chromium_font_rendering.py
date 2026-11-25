"""Check that restricting Local Font Access preserves ordinary webpage typography."""

import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import copyfile
from threading import Thread

import playwright
import pytest
from playwright.sync_api import sync_playwright

from chromium.check_local_fonts import POLICY_SWITCH
from chromium.native import default_binary, probe

PAGE = """<!doctype html><meta charset=utf-8>
<style>
  @font-face { font-family: TestIcons; src: url('/icons.ttf') format('truetype'); }
  p { width: 160px; margin: 0; font-size: 18px; line-height: 24px; }
  #mono { font-family: monospace; }
  #fallback { font-family: NonexistentMoltskinTestFont, monospace; }
  #serif { font-family: serif; }
  #icon { font: 32px TestIcons; }
</style>
<p id=mono></p><p id=fallback></p><p id=serif></p><span id=icon>&#xea60;</span>
<button>Count local fonts</button><output></output>
<script>
  for (const p of document.querySelectorAll('p'))
    p.textContent = 'Will this line wrap the same way when the first font is missing?';
  document.querySelector('button').onclick = async () => {
    const fonts = await window.queryLocalFonts();
    document.querySelector('output').textContent = fonts.length;
  };
</script>"""


@pytest.fixture
def font_page(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(PAGE, encoding="utf-8")
    # Playwright ships the Codicon font for its own tools. Serve its add icon
    # as a real web font without requiring network access or a system font path.
    font = next(Path(playwright.__file__).parent.rglob("codicon*.ttf"), None)
    assert font is not None, "Expected the installed Playwright package to contain Codicon"
    copyfile(font, site / "icons.ttf")
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=site)) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")
def test_local_font_restriction_preserves_fallback_wrapping_and_web_fonts(tmp_path, font_page):
    binary, _ = probe(default_binary())
    layouts = []
    with sync_playwright() as driver:
        for name, restricted in (("A", False), ("B", True), ("A", True), ("B", False)):
            with driver.chromium.launch_persistent_context(
                tmp_path / name, executable_path=str(binary), headless=True,
                args=[POLICY_SWITCH] if restricted else [],
            ) as context:
                page = context.pages[0]
                context.grant_permissions(["local-fonts"], origin=font_page)
                page.goto(font_page)
                page.get_by_role("button").click()
                page.wait_for_function("document.querySelector('output').textContent !== ''")
                count = int(page.locator("output").inner_text())
                assert (count == 0) == restricted, (name, restricted, count)

                # Confirm the loaded web font really supplied the rendered glyph.
                assert page.evaluate("""async () => {
                  const faces = await document.fonts.load('32px TestIcons', '\\uEA60');
                  return faces.length === 1 && faces[0].status === 'loaded';
                }""")
                session = context.new_cdp_session(page)
                session.send("DOM.enable")
                session.send("CSS.enable")
                root = session.send("DOM.getDocument")["root"]["nodeId"]
                icon = session.send("DOM.querySelector", {"nodeId": root, "selector": "#icon"})["nodeId"]
                fonts = session.send("CSS.getPlatformFontsForNode", {"nodeId": icon})["fonts"]
                assert any(font["isCustomFont"] and font["glyphCount"] > 0 for font in fonts)

                layout = page.locator("p").evaluate_all("""paragraphs => paragraphs.map(p => {
                  const text = document.createRange();
                  text.selectNodeContents(p);
                  const top = p.getBoundingClientRect().top;
                  return [...text.getClientRects()].map(r => [r.width, r.top - top, r.height]);
                })""")
                mono, fallback, serif = layout
                assert len(mono) > 1 and all(width > 0 and height > 0 for width, _, height in mono)
                assert fallback == mono
                assert serif != mono
                layouts.append(layout)
    assert all(layout == layouts[0] for layout in layouts)
