"""Rasterise a delivered archify diagram the way the committed PNGs were made.

Three things the bare-SVG capture gets wrong, and this does not:

* **The cards come too.** They carry the enumerations the picture deliberately leaves out --
  the five triggers, the six scaffolds -- so a capture of the `<svg>` alone loses the half a
  reader needs, and a diagram that has to be read alongside prose is not a diagram.
* **The viewer's own chrome does not.** Search, lenses, the theme switch and the export menu
  are the *reader's* controls; a still of them is a screenshot of an application, not of the
  subject. The viewer marks most of them `no-print`, which is the author's own list; the top
  toolbar is not in it and is hidden by name.
* **Dark, to match the four already committed.** A README that mixes themes reads as a
  README assembled from whatever was lying around.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME)
    page = browser.new_page(
        viewport={"width": 1600, "height": 1100}, device_scale_factor=2, color_scheme="dark"
    )
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"file://{src}")
    page.wait_for_selector("svg", timeout=15000)
    page.add_style_tag(content=".no-print, .toolbar { display: none !important; }")
    page.wait_for_timeout(1200)
    page.locator(".container").first.screenshot(path=str(out))
    browser.close()
print("wrote", out)
