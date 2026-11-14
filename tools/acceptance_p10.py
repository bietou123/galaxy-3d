"""P10: visible Moon navigation and accurate Solar System orbit spacing.

Run with: python tools/acceptance_p10.py
Requires Playwright and Pillow. Browser windows are always headless.
Use --edge --native-gpu to also check the installed Edge rendering path.
"""

import argparse
import math
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError, sync_playwright

import acceptance_p9 as base

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_p10"
check = base.check

AU = {
    "mercury": 0.387, "venus": 0.723, "earth": 1.0, "mars": 1.524,
    "jupiter": 5.204, "saturn": 9.583, "uranus": 19.191, "neptune": 30.07,
}

MOON_PIXELS = """() => new Promise(resolve => requestAnimationFrame(() => {
  const m=__galaxy.earthMoonInfo(), c=document.getElementById('gl');
  if (!m.moonXY || !m.moonPx) {resolve({visible:false});return;}
  const gl=c.getContext('webgl2') || c.getContext('webgl'), d=c.width/innerWidth;
  const r=m.moonPx*d*0.8, cx=m.moonXY[0]*d, cy=c.height-m.moonXY[1]*d;
  const x=Math.max(0,Math.floor(cx-r)), y=Math.max(0,Math.floor(cy-r));
  const w=Math.min(c.width-x,Math.ceil(2*r+2)), h=Math.min(c.height-y,Math.ceil(2*r+2));
  if (w<=0 || h<=0) {resolve({visible:false});return;}
  const p=new Uint8Array(w*h*4);
  gl.readPixels(x,y,w,h,gl.RGBA,gl.UNSIGNED_BYTE,p);
  let count=0, bright=0, sum=0, sum2=0;
  for(let j=0;j<h;j++) for(let i=0;i<w;i++) {
    if(Math.hypot(x+i-cx,y+j-cy)>r) continue;
    const k=(j*w+i)*4, value=(p[k]+p[k+1]+p[k+2])/3;
    count++; sum+=value; sum2+=value*value;
    if(value>35) bright++;
  }
  const mean=sum/Math.max(1,count);
  resolve({visible:count>200 && bright/count>0.1 && sum2/count-mean*mean>4,
    count, brightFraction:bright/Math.max(1,count), error:gl.getError()});
}))"""

PLANET_PIXELS = """id => new Promise(resolve => requestAnimationFrame(() => {
  const s=__galaxy.bodyScreen(id), c=document.getElementById('gl');
  if(!s) {resolve({visible:false});return;}
  const gl=c.getContext('webgl2') || c.getContext('webgl'), d=c.width/innerWidth;
  const x=Math.floor(s.x*d)-4, y=c.height-Math.floor(s.y*d)-4;
  if(x<0 || y<0 || x+9>c.width || y+9>c.height) {resolve({visible:false});return;}
  const p=new Uint8Array(9*9*4);
  gl.readPixels(x,y,9,9,gl.RGBA,gl.UNSIGNED_BYTE,p);
  let bright=0;
  for(let i=0;i<p.length;i+=4) if(Math.max(p[i],p[i+1],p[i+2])>50) bright++;
  resolve({visible:bright>=5,bright,error:gl.getError()});
}))"""


def moon_ready(page, part="moon", timeout=45000):
    try:
        page.wait_for_function("""part => {
          const d=__galaxy.dump();
          return d.state==='EARTHMOON_IDLE' && d.focusedEarthMoon===part
            && d.camEnabled && !__galaxy.camera().tweenActive
            && !__galaxy.navWant() && d.layers.earthmoon>0.99;
        }""", arg=part, polling=200, timeout=timeout)
        return True
    except TimeoutError:
        return False


def hash_to(page, target):
    page.evaluate("h => {location.hash=h;}", target)


def debug(page):
    return page.evaluate("() => ({hash:location.hash,state:__galaxy.dump().state,nav:__galaxy.navWant()})")


def orbit_info(page):
    return page.evaluate("() => __galaxy.solarOrbits()")


def fitted(bounds, frame):
    return bounds and bounds["left"] >= frame["left"] - 2 and bounds["right"] <= frame["right"] + 2 and bounds["top"] >= frame["top"] - 2 and bounds["bottom"] <= frame["bottom"] + 2


def test_moon(browser, errors):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = ctx.new_page()
    base.wire_errors(page, errors)
    base.boot(page, "?quality=low#solar")
    base.settle(page, "SOLAR_IDLE")
    renderer = page.evaluate("""() => {
      const canvas=document.getElementById('gl');
      const gl=canvas.getContext('webgl2') || canvas.getContext('webgl');
      const ext=gl.getExtension('WEBGL_debug_renderer_info');
      return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    }""")
    print("Renderer: " + renderer, flush=True)
    entry = page.locator("#btnMoon")
    check("M01 Solar System has a visible direct Moon entry", entry.count() == 1 and entry.is_visible())

    hash_to(page, "earthmoon/moon")
    ok = moon_ready(page, timeout=20000)
    check("M02 same-document Moon link from the Solar System completes navigation", ok, debug(page))
    if not ok:
        page.screenshot(path=str(OUT / "missing-moon.png"))
        ctx.close()
        return
    check("M03 Moon link ends at the Moon, not the intermediate Earth focus", page.evaluate("location.hash") == "#earthmoon/moon" and page.locator("#infoZh").inner_text() == "\u6708\u7403")
    check("M03 browser tab identifies the Moon view", "\u6708\u7403" in page.title())
    check("M03 Moon controls immediately reflect the focused view", page.locator("#btnFollowMoon.on").count() == 1 and not page.locator("#btnFarSide").is_disabled() and "\u505c\u6b62" in page.locator("#btnFollowMoon").inner_text())
    pixels = page.evaluate(MOON_PIXELS)
    check("M04 actual Moon disc contains rendered surface pixels", pixels["visible"] and pixels["error"] == 0, pixels)
    page.screenshot(path=str(OUT / "desktop-moon.png"))

    page.locator('[data-mrate="8"]').click()
    visible = True
    centered = True
    maximum_lag = 0
    for _ in range(4):
        base.frames(page, 8)
        snapshot = page.evaluate("() => ({moon:__galaxy.earthMoonInfo(),target:__galaxy.camera().target})")
        lag = math.dist(snapshot["target"], snapshot["moon"]["moonPos"])
        maximum_lag = max(maximum_lag, lag)
        centered = centered and lag < 0.02
        visible = visible and page.evaluate(MOON_PIXELS)["visible"]
    check("M05 close Moon view remains visible at 8x speed", visible)
    check("M05 following the Moon does not lag behind its orbit", centered, maximum_lag)
    page.locator('[data-mrate="1"]').click()

    page.locator("#btnBack").click()
    base.settle(page, "SOLAR_IDLE")
    page.locator('#bodyList li[data-id="earth"]').click()
    base.settle(page, "PLANET_FOCUS")
    check("M06 Moon entry stays available while Earth is focused", entry.is_visible())
    entry.click()
    check("M06 clicking the visible Moon entry focuses the Moon", moon_ready(page), debug(page))
    page.locator("#btnBack").click()
    base.settle(page, "PLANET_FOCUS")
    check("M07 returning from the Moon preserves the previous Earth view", page.locator("#infoZh").inner_text() == "\u5730\u7403")

    page.locator("#btnReset").click()
    base.settle(page, "SOLAR_IDLE")
    page.locator('#bodyList li[data-id="mars"]').click()
    base.wait(page, "() => __galaxy.dump().state==='TRANSITION'")
    hash_to(page, "earthmoon/moon")
    check("M08 Moon link during a planet transition is queued, not ignored", moon_ready(page), debug(page))
    hash_to(page, "earthmoon/earth")
    check("M09 Earth part link works from an already open Moon view", moon_ready(page, "earth"), debug(page))
    hash_to(page, "earthmoon/moon")
    hash_to(page, "earthmoon/earth")
    check("M09 a newer part link replaces the older destination", moon_ready(page, "earth"), debug(page))
    hash_to(page, "earthmoon/unknown")
    base.frames(page, 3)
    check("M10 invalid Moon part does not fabricate a destination", page.evaluate("() => __galaxy.dump().focusedEarthMoon") == "earth")
    hash_to(page, "earthmoon")
    base.settle(page, "EARTHMOON_IDLE", "focusedEarthMoon", None)
    check("M10 unqualified Earth-Moon link restores the whole-system view", page.locator("#info.show").count() == 0)

    base.boot(page, "?quality=low#earthmoon/moon")
    check("M11 cold Moon deep link still reaches the Moon", moon_ready(page), debug(page))
    base.search(page, "Sirius", "sirius")
    base.settle(page, "STAR_IDLE", "focusedStar", "sirius")
    hash_to(page, "earthmoon/moon")
    check("M12 Moon link can leave an unrelated star system", moon_ready(page), debug(page))
    ctx.close()


def test_orbits(browser, errors):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = ctx.new_page()
    base.wire_errors(page, errors)
    base.boot(page, "?quality=low#solar")
    base.settle(page, "SOLAR_IDLE")
    available = page.evaluate("() => typeof __galaxy.solarOrbits==='function'")
    check("O01 orbit geometry has a read-only verification probe", available)
    if not available:
        ctx.close()
        return
    info = orbit_info(page)
    check("O01 true AU spacing is the default", info["mode"] == "real")
    for b in info["bodies"]:
        expected = AU[b["id"]] * 30
        check("O02 " + b["id"] + " orbit radius follows its mean AU distance", abs(b["au"] - AU[b["id"]]) < 1e-9 and abs(b["orbitR"] - expected) < 1e-9, b)
        check("O03 " + b["id"] + " line geometry and moving planet share the same orbit", abs(b["worldR"] - expected) < 1e-6 and b["lineError"] < 0.001, b)
    bodies = {b["id"]: b for b in info["bodies"]}
    check("O04 Neptune/Earth distance ratio is 30.07, not a compressed ratio", abs(bodies["neptune"]["orbitR"] / bodies["earth"]["orbitR"] - 30.07) < 1e-9)
    gaps = [b["orbitR"] - a["orbitR"] for a, b in zip(info["bodies"], info["bodies"][1:])]
    expected = [(b - a) * 30 for a, b in zip(AU.values(), list(AU.values())[1:])]
    check("O04 all seven adjacent orbit gaps preserve AU spacing", all(abs(a - b) < 1e-9 for a, b in zip(gaps, expected)), gaps)
    base.wait(page, "() => __galaxy.scale().note.includes('\u771f\u5b9e')")
    scale = page.evaluate("() => __galaxy.scale()")
    check("O05 true-distance scale distinguishes enlarged body sizes", "\u771f\u5b9e" in scale["note"] and "\u653e\u5927" in scale["note"] and "AU" in scale["label"], scale)
    check("O05 scale controls have a single accessible selection", page.locator('[data-solar-orbits="real"]').get_attribute("aria-pressed") == "true" and page.locator('[data-solar-orbits="compressed"]').get_attribute("aria-pressed") == "false")
    for planet in ["jupiter", "saturn", "uranus", "neptune"]:
        pixels = page.evaluate(PLANET_PIXELS, planet)
        check("O05 " + planet + " remains visibly marked at true distances", pixels["visible"] and pixels["error"] == 0, pixels)
    labels = page.locator(".lbl.k-solar").evaluate_all("els => els.filter(e=>Number(getComputedStyle(e).opacity)>0.2).map(e=>e.textContent)")
    check("O05 distant planets retain readable scene labels", len(labels) >= 4, labels)

    page.locator("#btnPause").click()
    old = orbit_info(page)
    page.locator('[data-solar-orbits="compressed"]').click()
    info = orbit_info(page)
    check("O06 illustrative mode is an explicit user choice", info["mode"] == "compressed")
    check("O06 mode switch updates planets even when time is paused", all(abs(b["orbitR"] - 30 * AU[b["id"]] ** 0.55) < 1e-9 and abs(b["worldR"] - b["orbitR"]) < 1e-6 and b["lineError"] < 0.001 for b in info["bodies"]), info)
    check("O07 changing spacing does not advance orbital phases", all(abs(a["angle"] - b["angle"]) < 1e-9 for a, b in zip(old["bodies"], info["bodies"])))
    base.wait(page, "() => __galaxy.scale().note.includes('\u538b\u7f29')")
    check("O07 illustrative scale discloses its local approximation", "1 AU" in page.evaluate("() => __galaxy.scale().note"))
    page.locator('[data-solar-orbits="real"]').click()
    restored = orbit_info(page)
    check("O08 returning to true mode restores every orbit and buffer", restored["mode"] == "real" and all(abs(b["orbitR"] - 30 * AU[b["id"]]) < 1e-9 and b["lineError"] < 0.001 for b in restored["bodies"]))

    page.locator('[data-solar-view="inner"]').click()
    base.settle(page, "SOLAR_IDLE")
    info = orbit_info(page)
    check("O09 inner-planet view preserves true spacing", info["mode"] == "real" and info["view"] == "inner")
    check("O09 all four inner orbits fit the available frame", all(fitted(b["screenBounds"], info["frame"]) for b in info["bodies"][:4]), info["frame"])
    page.screenshot(path=str(OUT / "desktop-inner-planets.png"))
    page.locator('[data-solar-view="all"]').click()
    base.settle(page, "SOLAR_IDLE")
    info = orbit_info(page)
    check("O10 whole-system view restores the outer planets", info["view"] == "all" and all(fitted(b["screenBounds"], info["frame"]) for b in info["bodies"]))

    page.locator('#bodyList li[data-id="neptune"]').click()
    base.settle(page, "PLANET_FOCUS")
    page.locator('[data-solar-orbits="compressed"]').click()
    base.frames(page, 3)
    screen = page.evaluate("() => __galaxy.bodyScreen('neptune')")
    frame = page.evaluate("() => __galaxy.sceneFrame()")
    check("O11 switching spacing while focused does not lose the planet", page.locator("#infoZh").inner_text() == "\u6d77\u738b\u661f" and math.hypot(screen["x"] - frame["x"], screen["y"] - frame["y"]) < 1, screen)
    page.locator('[data-solar-orbits="real"]').click()
    page.locator("#btnReset").click()
    base.settle(page, "SOLAR_IDLE")
    page.locator("#btnPause").click()
    page.locator('#bodyList li[data-id="earth"]').click()
    base.settle(page, "PLANET_FOCUS")
    page.locator('[data-speed="100"]').click()
    base.frames(page, 20)
    screen = page.evaluate("() => __galaxy.bodyScreen('earth')")
    frame = page.evaluate("() => __galaxy.sceneFrame()")
    check("O12 focused planets remain centered at high time speed", math.hypot(screen["x"] - frame["x"], screen["y"] - frame["y"]) < 1, screen)
    ctx.close()


def test_layout(browser, errors):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce", has_touch=True)
    page = ctx.new_page()
    base.wire_errors(page, errors)
    base.boot(page, "?quality=low#solar")
    base.settle(page, "SOLAR_IDLE")
    for width, height in [(1280, 800), (390, 844), (320, 568), (844, 390)]:
        label = "solar-%dx%d" % (width, height)
        page.set_viewport_size({"width": width, "height": height})
        base.frames(page, 4)
        layout = page.evaluate(base.LAYOUT)
        check("R01 " + label + " panels do not overlap or leave the screen", not layout["overlaps"] and not layout["outside"] and not layout["pageScroll"], layout)
        controls = page.locator("#solarControls button").evaluate_all("els => els.every(e => e.scrollWidth<=e.clientWidth+1 && e.getBoundingClientRect().height<=34)")
        check("R02 " + label + " Moon and orbit controls stay visible and single-line", controls and page.locator("#btnMoon").is_visible())
        info = orbit_info(page)
        check("R03 " + label + " all eight orbits remain framed", all(fitted(b["screenBounds"], info["frame"]) for b in info["bodies"]), info)
        pixels = page.evaluate(base.PIXELS)
        check("R04 " + label + " has real nonblank WebGL output", pixels["bright"] > 500 and pixels["error"] == 0, pixels)
        page.screenshot(path=str(OUT / (label + ".png")))

    page.locator("#btnMoon").click()
    check("R05 compact visible Moon entry reaches the Moon", moon_ready(page), debug(page))
    for width, height in [(1280, 800), (390, 844), (320, 568), (844, 390)]:
        label = "moon-%dx%d" % (width, height)
        page.set_viewport_size({"width": width, "height": height})
        base.frames(page, 4)
        layout = page.evaluate(base.LAYOUT)
        check("R06 " + label + " panels do not overlap or leave the screen", not layout["overlaps"] and not layout["outside"], layout)
        pixels = page.evaluate(MOON_PIXELS)
        check("R07 " + label + " visibly renders the Moon surface", pixels["visible"] and pixels["error"] == 0, pixels)
        frame = page.evaluate("() => __galaxy.sceneFrame()")
        m = page.evaluate("() => __galaxy.earthMoonInfo()")
        bounds = {"left": m["moonXY"][0] - m["moonPx"], "right": m["moonXY"][0] + m["moonPx"], "top": m["moonXY"][1] - m["moonPx"], "bottom": m["moonXY"][1] + m["moonPx"]}
        check("R08 " + label + " complete Moon disc stays clear of controls", fitted(bounds, frame), bounds)
        page.screenshot(path=str(OUT / (label + ".png")))
    ctx.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["moon", "orbits", "layout"])
    parser.add_argument("--edge", action="store_true")
    parser.add_argument("--native-gpu", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        options = {"headless": True}
        if args.edge:
            options["channel"] = "msedge"
        if not args.native_gpu:
            options["args"] = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-gpu-sandbox"]
        browser = pw.chromium.launch(**options)
        try:
            for name, test in [("moon", test_moon), ("orbits", test_orbits), ("layout", test_layout)]:
                if args.only is None or args.only == name:
                    test(browser, errors)
        finally:
            browser.close()
    check("E01 no page or console errors", not errors, errors[:5])
    print("\n%d passed, %d failed" % (base.passed, base.failed), flush=True)
    return 0 if base.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
