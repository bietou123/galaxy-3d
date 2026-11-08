"""Headless smoke test for galaxy-3d.

Loads index.html in a headless Chromium (software WebGL), waits for the
boot overlay to disappear, then checks rendering and reports any page or
console errors. Never opens a visible window.

Usage:
    pip install playwright
    python -m playwright install chromium
    python tools/smoke_test.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("PASS  " + name)
    else:
        failed += 1
        print("FAIL  " + name + ("  (" + detail + ")" if detail else ""))


def main():
    check("index.html exists", PAGE.is_file())
    if not PAGE.is_file():
        print("")
        print("%d passed, %d failed" % (passed, failed))
        return 1

    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--disable-gpu-sandbox",
            ],
        )
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda exc: errors.append("pageerror: " + str(exc)))
        page.on(
            "console",
            lambda msg: errors.append("console: " + msg.text)
            if msg.type == "error"
            else None,
        )

        page.goto(PAGE.as_uri())

        try:
            page.wait_for_selector("#boot.gone", state="attached", timeout=30000)
            booted = True
        except Exception:
            booted = False

        check("boot overlay completes", booted)

        fail_visible = page.evaluate(
            "() => { const f = document.getElementById('fail');"
            " return !!f && getComputedStyle(f).display !== 'none'; }"
        )
        check("no WebGL failure screen", not fail_visible)

        size = page.evaluate(
            "() => { const c = document.getElementById('gl');"
            " return { w: c.width, h: c.height }; }"
        )
        check("canvas has non-zero size", size["w"] > 0 and size["h"] > 0,
              "%dx%d" % (size["w"], size["h"]))

        gl_ok = page.evaluate(
            "() => { const c = document.getElementById('gl');"
            " return !!(window.WebGL2RenderingContext || window.WebGLRenderingContext)"
            " && !!(c.getContext('webgl2') || c.getContext('webgl')); }"
        )
        check("WebGL context available", gl_ok)

        page.wait_for_timeout(1200)
        first = page.screenshot()
        page.wait_for_timeout(900)
        second = page.screenshot()
        check("render loop is animating", first != second)

        page.screenshot(path=str(ROOT / "_shot_headless.png"))
        shot = (ROOT / "_shot_headless.png").stat().st_size
        check("screenshot has scene detail", shot > 50000, "%d bytes" % shot)

        browser.close()

    check("no page/console errors", not errors, "; ".join(errors[:3]))

    print("")
    print("%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
