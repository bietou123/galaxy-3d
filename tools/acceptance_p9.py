"""P9: cross-layer search, quality preferences, and responsive layout.

Run with: python tools/acceptance_p9.py
Requires Playwright (Chromium) and Pillow. All windows are headless.
Quality policy tests use synthetic rAF timestamps, not GPU benchmarks.
Screenshots and pixel checks use the real render loop with quality=low.
"""

import argparse
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
OUT = ROOT / "_p9"
STORAGE_KEY = "galaxy-3d:quality"
passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("PASS  " + name, flush=True)
    else:
        failed += 1
        print("FAIL  " + name + ("  " + str(detail) if detail else ""), flush=True)


def wait(page, predicate, arg=None):
    page.wait_for_function(predicate, arg=arg, polling=200, timeout=90000)


def boot(page, suffix="?quality=low"):
    page.goto(PAGE.as_uri() + suffix)
    page.wait_for_selector("#boot.gone", state="attached", timeout=90000)
    page.add_style_tag(content="*{transition:none!important}#toast{display:none!important}")


def settle(page, state=None, field=None, value=None):
    wait(page, """([state, field, value]) => {
      const g = window.__galaxy, d = g.dump(), c = g.camera();
      return (!state || d.state === state) && (!field || d[field] === value)
        && d.camEnabled && !c.tweenActive && !g.navWant()
        && Object.values(d.layers).filter(a => a > 0.99).length === 1;
    }""", [state, field, value])


def frames(page, count=6):
    page.evaluate("""n => new Promise(resolve => {
      function tick() { if (--n <= 0) resolve(); else requestAnimationFrame(tick); }
      requestAnimationFrame(tick);
    })""", count)


def results(page, query):
    page.locator("#objectSearch").fill(query)
    return page.locator(".searchResult").evaluate_all("els => els.map(e => e.dataset.objectId)")


def search(page, query, object_id=None, click=False):
    ids = results(page, query)
    if object_id is not None and object_id not in ids:
        raise AssertionError("Missing search target: " + object_id + " / " + str(ids))
    if click:
        page.locator('.searchResult[data-object-id="' + object_id + '"]').click()
    else:
        if object_id is not None and ids[0] != object_id:
            raise AssertionError("Wrong first search result: " + str(ids))
        page.locator("#objectSearch").press("Enter")


def quality(page):
    return page.evaluate("() => window.__galaxy.quality()")


def wire_errors(page, errors):
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)


LAYOUT = """() => {
  const ids = ['brand', 'sidebar', 'bar', 'moonBar', 'moonBadge', 'info', 'scale', 'legend'];
  const rects = [], overlaps = [], outside = [], clipped = [];
  function visible(el) {
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) > 0.05;
  }
  for (const id of ids) {
    const el = document.getElementById(id), r = el.getBoundingClientRect();
    if (!visible(el) || r.width === 0 || r.height === 0) continue;
    rects.push({id, x:r.x, y:r.y, right:r.right, bottom:r.bottom, w:r.width, h:r.height});
    if (r.x < -1 || r.y < -1 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1)
      outside.push(id);
  }
  for (let i=0; i<rects.length; i++) for (let j=i+1; j<rects.length; j++) {
    const a=rects[i], b=rects[j];
    if (Math.min(a.right,b.right)-Math.max(a.x,b.x)>1 &&
        Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1) overlaps.push([a.id,b.id]);
  }
  for (const el of document.querySelectorAll('#bar button, #moonBar button, #qualitySet button')) {
    if (visible(el) && (el.scrollWidth > el.clientWidth + 1 ||
        (el.closest('#qualitySet') && el.getBoundingClientRect().height > 30)))
      clipped.push(el.id || el.textContent);
  }
  return {rects, overlaps, outside, clipped,
    pageScroll:document.documentElement.scrollWidth > innerWidth};
}"""


PIXELS = """() => new Promise(resolve => requestAnimationFrame(() => {
  const c=document.getElementById('gl'), gl=c.getContext('webgl2') || c.getContext('webgl');
  const p=new Uint8Array(c.width*c.height*4);
  gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,p);
  let bright=0, hash=2166136261, colors=new Set();
  for (let i=0;i<p.length;i+=4) {
    if (Math.max(p[i],p[i+1],p[i+2])>40) bright++;
    if (i%64===0) {
      colors.add((p[i]<<16)|(p[i+1]<<8)|p[i+2]);
      hash=Math.imul(hash ^ ((p[i]<<16)|(p[i+1]<<8)|p[i+2]),16777619);
    }
  }
  resolve({bright,colors:colors.size,hash:hash>>>0,error:gl.getError()});
}))"""


CLOCK = """(() => {
  const raf=window.requestAnimationFrame.bind(window);
  let previous=-1, clock=1;
  window.__frameStep=0.04;
  window.__frameTicks=0;
  window.requestAnimationFrame=cb => raf(stamp => {
    if (stamp!==previous) {
      previous=stamp; clock+=window.__frameStep*1000; window.__frameTicks++;
    }
    cb(clock);
  });
})()"""


def test_search(browser, errors):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = ctx.new_page()
    wire_errors(page, errors)
    boot(page)
    settle(page, "GALAXY_IDLE")
    cases = [
        ("Chinese planet", "\u5730\u7403", "earth"),
        ("English planet", "Earth", "earth"),
        ("catalog number", "M31", "m31"),
        ("alternate catalog", "NGC 224", "m31"),
        ("full-width input", "\uff2d\uff13\uff11", "m31"),
        ("English star", "Sirius", "sirius"),
        ("component alias", "\u5929\u72fc\u661f B", "sirius"),
        ("planet identifier", "proxima b", "proxima-b"),
    ]
    for label, query, target in cases:
        ids = results(page, query)
        check("S01 " + label, target in ids, ids)
    ids = results(page, "barnard-b")
    text = page.locator("#searchResults").inner_text()
    check("S02 candidates retain their status", ids == ["barnard-b"] and "\u5019\u9009\u884c\u661f" in text, text)
    ids = results(page, "undefined")
    check("S03 no fabricated or undefined entries", not ids and page.locator(".searchEmpty").count() == 1)
    page.locator("#objectSearch").press("Escape")
    check("S03 clearing search restores the navigator", page.locator("#sidebar.searching").count() == 0 and page.locator("#qualitySet").is_visible())

    page.locator("#collapse").click()
    page.keyboard.press("Control+k")
    check("S04 shortcut expands the collapsed navigator", page.locator("#objectSearch").evaluate("e => e === document.activeElement") and page.locator("#sidebar.collapsed").count() == 0)
    results(page, "\u661f")
    aria = page.locator("#objectSearch").get_attribute("aria-activedescendant")
    page.locator("#objectSearch").press("ArrowDown")
    active = page.locator("#objectSearch").get_attribute("aria-activedescendant")
    check("S05 arrow selection updates accessible focus", aria != active and page.locator("#" + active).get_attribute("aria-selected") == "true")
    check("S05 combobox and listbox roles agree", page.locator("#objectSearch").get_attribute("role") == "combobox" and page.locator("#searchResults").get_attribute("role") == "listbox")

    results(page, "M31")
    page.locator("#objectSearch").evaluate("e => e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true}))")
    check("S06 IME confirmation does not navigate", page.evaluate("() => __galaxy.navWant()") is None and page.locator("#objectSearch").input_value() == "M31")
    cam = page.evaluate("() => __galaxy.camera()")
    pause = page.locator("#btnPause").inner_text()
    speed = page.locator("#speeds .on").inner_text()
    page.locator("#objectSearch").fill("")
    page.locator("#objectSearch").press_sequentially("wasd h/?123[]+- ")
    frames(page)
    after = page.evaluate("() => __galaxy.camera()")
    check("S07 text input does not pan or change view", cam["tTarget"] == after["tTarget"] and cam["tPhi"] == after["tPhi"] and cam["tRadius"] == after["tRadius"])
    check("S07 text input does not pause, change speed, or open help/demo", pause == page.locator("#btnPause").inner_text() and speed == page.locator("#speeds .on").inner_text() and not page.evaluate("() => __galaxy.demo().active") and page.locator("#help.show").count() == 0)
    page.locator("#objectSearch").press("Escape")
    check("S07 search Escape leaves the current layer intact", page.evaluate("() => __galaxy.dump().state") == "GALAXY_IDLE")

    page.keyboard.down("w")
    wait(page, "old => __galaxy.camera().tTarget.some((n,i) => n !== old[i])", cam["tTarget"])
    page.locator("#objectSearch").focus()
    target = page.evaluate("() => __galaxy.camera().tTarget")
    frames(page, 8)
    check("S08 focusing search clears held movement keys", target == page.evaluate("() => __galaxy.camera().tTarget"))
    page.keyboard.up("w")

    search(page, "M31", "m31", click=True)
    settle(page, "LOCALGROUP_IDLE", "focusedGalaxy", "m31")
    check("S09 clicked search result crosses to the Local Group", page.locator("#infoZh").inner_text() == "\u4ed9\u5973\u5ea7\u661f\u7cfb")
    search(page, "proxima-b", "proxima-b")
    settle(page, "STAR_IDLE", "focusedSystem", "proxima-b")
    check("S10 group-to-exoplanet navigation reaches the correct system", page.evaluate("() => __galaxy.dump().focusedStar") == "alphacen")
    search(page, "Alpha Centauri", "alphacen")
    settle(page, "STAR_IDLE", "focusedSystem", None)
    check("S11 searching the current system clears planet focus", page.locator("#infoZh").inner_text() == "\u5357\u95e8\u4e8c")
    search(page, "Sirius", "sirius")
    settle(page, "STAR_IDLE", "focusedStar", "sirius")
    check("S12 star-to-star navigation returns through the galaxy", page.evaluate("() => __galaxy.dump().focusedSystem") is None)
    search(page, "Earth", "earth")
    settle(page, "PLANET_FOCUS")
    check("S13 star-to-Solar-System search focuses Earth", page.locator("#infoZh").inner_text() == "\u5730\u7403")
    search(page, "Moon", "moon")
    settle(page, "EARTHMOON_IDLE", "focusedEarthMoon", "moon")
    check("S14 Moon search reaches the Earth-Moon layer", page.evaluate("() => location.hash") == "#earthmoon/moon")
    search(page, "solar-system", "solar-system")
    settle(page, "SOLAR_IDLE")
    check("S15 Solar System search restores the system overview", page.locator("#info.show").count() == 0)
    search(page, "Milky Way", "milkyway")
    settle(page, "GALAXY_IDLE")
    check("S16 Milky Way search uses the full galaxy view", page.evaluate("() => __galaxy.layerNow()") == "galaxy")

    search(page, "M31", "m31")
    wait(page, "() => __galaxy.dump().state === 'TRANSITION'")
    search(page, "Mars", "mars")
    settle(page, "PLANET_FOCUS")
    check("S17 replacing a target during a transition reaches the latest result", page.locator("#infoZh").inner_text() == "\u706b\u661f" and page.evaluate("() => location.hash") == "#body/mars")
    search(page, "M31", "m31")
    wait(page, "() => __galaxy.dump().state === 'TRANSITION'")
    page.locator("#gl").dispatch_event("pointerdown", {"pointerId": 23, "pointerType": "touch", "clientX": 640, "clientY": 240})
    page.locator("#gl").dispatch_event("pointerup", {"pointerId": 23, "pointerType": "touch", "clientX": 640, "clientY": 240})
    settle(page, "GALAXY_IDLE")
    frames(page, 10)
    check("S18 user gestures cancel remaining search navigation", page.evaluate("() => __galaxy.dump().state") == "GALAXY_IDLE" and page.evaluate("() => __galaxy.navWant()") is None)

    search(page, "Moon", "moon")
    wait(page, "() => __galaxy.dump().state === 'TRANSITION'")
    page.keyboard.press("Escape")
    settle(page, "SOLAR_IDLE")
    frames(page, 8)
    check("S19 Escape cancels the remaining queued steps", page.evaluate("() => __galaxy.dump().state") == "SOLAR_IDLE")
    page.locator('[data-quality="mid"]').click()
    check("S20 visible quality buttons use the same preference path", quality(page)["mode"] == "mid" and page.locator('[data-quality="mid"]').get_attribute("aria-pressed") == "true")
    boot(page, "?quality=low#earthmoon/moon")
    search(page, "M31", "m31")
    settle(page, "LOCALGROUP_IDLE", "focusedGalaxy", "m31")
    check("S21 searching during a cold deep-link transition replaces the pending destination", page.evaluate("() => location.hash") == "#localgroup/m31")
    ctx.close()


def test_quality(browser, errors):
    ctx = browser.new_context(viewport={"width": 640, "height": 480})
    ctx.add_init_script(CLOCK)
    page = ctx.new_page()
    wire_errors(page, errors)
    boot(page, "")
    check("Q01 default mode is automatic", quality(page)["mode"] == "auto")
    wait(page, "() => __galaxy.quality().windows >= 1")
    q = quality(page)
    check("Q02 sustained 40ms frames select medium quality", q["level"] == "mid" and q["galaxyParticles"] == 204000 and abs(q["averageMs"] - 40) < 0.01, q)
    page.evaluate("() => { window.__frameStep=0.15; }")
    wait(page, "() => __galaxy.quality().level === 'low'")
    q = quality(page)
    check("Q03 later slowdowns lower quality without a reload", q["mode"] == "auto" and q["windows"] >= 2 and 118999 <= q["galaxyParticles"] <= 119000, q)
    check("Q03 real frame sampling is not clipped to the physics timestep", q["averageMs"] > 100, q)
    check("Q04 automatic button and tooltip follow the applied level", page.locator('[data-quality="auto"]').get_attribute("aria-pressed") == "true" and "\u4f4e" in page.locator('[data-quality="auto"]').get_attribute("title"))
    old_windows = q["windows"]
    page.evaluate("() => { window.__frameStep=0.02; }")
    wait(page, "n => __galaxy.quality().windows >= n + 2", old_windows)
    check("Q05 faster frames do not cause oscillating upgrades", quality(page)["level"] == "low")
    page.evaluate("() => { __galaxy.setQuality('high'); window.__frameStep=0.15; }")
    frames(page, 30)
    q = quality(page)
    check("Q06 manual high quality stays locked on a slow device", q["level"] == "high" and q["locked"] and q["windows"] == 0, q)
    invalid = page.evaluate("() => __galaxy.setQuality('broken')")
    check("Q07 invalid quality input leaves the current state untouched", invalid is None and quality(page) == q)
    saved = page.evaluate("key => JSON.parse(localStorage.getItem(key))", STORAGE_KEY)
    check("Q08 manual choices are stored as versioned preferences", saved == {"version": 1, "mode": "high"}, saved)
    check("Q08 high-quality particle budget matches the built model", q["galaxyParticles"] == 340000, q)
    boot(page, "")
    check("Q09 reload restores manual quality", quality(page)["mode"] == "high" and quality(page)["locked"])
    boot(page, "?quality=low")
    check("Q10 URL quality takes priority without changing the saved preference", quality(page)["level"] == "low" and page.evaluate("key => JSON.parse(localStorage.getItem(key)).mode", STORAGE_KEY) == "high")
    boot(page, "")
    check("Q10 removing the URL override restores the preference", quality(page)["level"] == "high")
    boot(page, "?quality=low-invalid")
    check("Q11 malformed URL values are not partial matches", quality(page)["mode"] == "high")
    page.evaluate("() => __galaxy.setQuality('mid')")
    boot(page, "?quality=%68%69%67%68")
    check("Q11 URL query decoding uses the standard parser", quality(page)["mode"] == "high")

    for label, data in [("malformed JSON", "{"), ("unknown mode", '{"version":1,"mode":"broken"}'), ("unknown version", '{"version":2,"mode":"low"}')]:
        page.evaluate("([key, data]) => localStorage.setItem(key,data)", [STORAGE_KEY, data])
        boot(page, "")
        check("Q12 " + label + " falls back to automatic mode", quality(page)["mode"] == "auto")

    page.evaluate("() => { __galaxy.setQuality('auto'); window.__frameStep=0.02; }")
    page.evaluate("""() => {
      window.__testHidden=true;
      Object.defineProperty(document,'hidden',{configurable:true,get:() => window.__testHidden});
      document.dispatchEvent(new Event('visibilitychange'));
      window.__frameStep=20;
    }""")
    angle = page.evaluate("() => __galaxy.earthMoonInfo().angle")
    frames(page, 8)
    check("Q13 hidden pages do not advance the simulation or quality sampler", angle == page.evaluate("() => __galaxy.earthMoonInfo().angle") and quality(page)["frames"] == 0)
    page.evaluate("() => { window.__testHidden=false; window.__frameStep=0.02; document.dispatchEvent(new Event('visibilitychange')); }")
    frames(page, 10)
    q = quality(page)
    check("Q14 returning to the page discards the hidden time gap", q["level"] == "high" and q["frames"] == 0 and q["warmupSeconds"] > 0.5, q)
    check("Q14 animation resumes after visibility returns", angle != page.evaluate("() => __galaxy.earthMoonInfo().angle"))
    saved = page.evaluate("key => JSON.parse(localStorage.getItem(key)).mode", STORAGE_KEY)
    check("Q15 automatic mode, not a transient level, is persisted", saved == "auto")
    ctx.close()

    ctx = browser.new_context(viewport={"width": 640, "height": 480}, device_scale_factor=3)
    ctx.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Disabled','SecurityError')}})")
    page = ctx.new_page()
    wire_errors(page, errors)
    boot(page)
    check("Q16 disabled storage does not prevent boot or URL override", quality(page)["mode"] == "low")
    check("Q17 low quality caps the actual canvas pixel ratio", page.locator("#gl").evaluate("e => e.width") == 640)
    page.evaluate("() => __galaxy.setQuality('mid')")
    check("Q16 changing quality still works when storage is disabled", quality(page)["mode"] == "mid")
    check("Q17 medium quality caps the actual canvas pixel ratio", page.locator("#gl").evaluate("e => e.width") == 800)
    page.evaluate("() => __galaxy.setQuality('high')")
    check("Q17 high quality caps the actual canvas pixel ratio", page.locator("#gl").evaluate("e => e.width") == 1280)
    ctx.close()


def layout_shot(page, label, focused=None):
    frames(page, 3)
    layout = page.evaluate(LAYOUT)
    check("L01 " + label + " panels do not overlap", not layout["overlaps"], layout["overlaps"])
    check("L02 " + label + " panels stay inside the viewport", not layout["outside"] and not layout["pageScroll"], layout["outside"])
    check("L03 " + label + " button labels fit", not layout["clipped"], layout["clipped"])
    shot = page.screenshot(path=str(OUT / (label + ".png")))
    img = Image.open(io.BytesIO(shot)).convert("RGB")
    colors = img.getextrema()
    px = page.evaluate(PIXELS)
    check("L04 " + label + " scene pixels contain rendered detail", px["bright"] > 500 and px["colors"] > 20 and px["error"] == 0 and all(hi > lo for lo, hi in colors), px)
    if focused:
        box = page.evaluate("""id => {
          const g=__galaxy, f=g.sceneFrame();
          let p;
          if (id==='moon') { const em=g.earthMoonInfo(); p={x:em.moonXY[0],y:em.moonXY[1],px:em.moonPx}; }
          else p=g.bodyScreen(id);
          return {f,p};
        }""", focused)
        f, p = box["f"], box["p"]
        visible = p and p["px"] >= 12 and p["x"] - p["px"] >= f["left"] - 2 and p["x"] + p["px"] <= f["right"] + 2 and p["y"] - p["px"] >= f["top"] - 2 and p["y"] + p["px"] <= f["bottom"] + 2
        check("L09 " + label + " focused object is not hidden behind controls", visible, box)


def test_layout(browser, errors):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce", has_touch=True)
    page = ctx.new_page()
    wire_errors(page, errors)
    boot(page)
    settle(page, "GALAXY_IDLE")
    layout_shot(page, "desktop-galaxy")
    for width, height in [(390, 844), (320, 568), (844, 390)]:
        page.set_viewport_size({"width": width, "height": height})
        layout_shot(page, "galaxy-%dx%d" % (width, height))
        point = page.evaluate("() => {const f=__galaxy.sceneFrame();return [f.x+20,f.y+15];}")
        ray = page.evaluate("p => __galaxy.screenToGalaxy(p[0],p[1])", point)
        check("L13 mobile galaxy projection round trip at %dx%d" % (width, height), ray is not None and abs(ray["x"] - point[0]) < 0.1 and abs(ray["y"] - point[1]) < 0.1, ray)
        page.mouse.click(*point)
        settle(page, "GALAXY_IDLE")
        target = page.evaluate("() => __galaxy.camera().target")
        check("L14 clicking the mobile galaxy aims at the visible point at %dx%d" % (width, height), ray is not None and max(abs(a-b) for a, b in zip(target, ray["world"])) < 0.5, target)
        page.keyboard.press("r")
        settle(page, "GALAXY_IDLE")
    page.set_viewport_size({"width": 320, "height": 568})
    frames(page, 3)
    page.evaluate("() => __galaxy.aimAtSun(250)")
    frames(page, 2)
    dive = page.evaluate("() => __galaxy.tryDive()")
    check("L15 aligned Sun can still be entered on a compact viewport", dive)
    settle(page, "SOLAR_IDLE")
    page.evaluate("() => __galaxy.goToLayer('galaxy')")
    settle(page, "GALAXY_IDLE")
    page.set_viewport_size({"width": 1280, "height": 800})
    frames(page, 3)
    before = page.evaluate(PIXELS)
    frames(page, 8)
    after = page.evaluate(PIXELS)
    check("L05 actual canvas output animates", before["hash"] != after["hash"])
    theta = page.evaluate("() => __galaxy.camera().tTheta")
    page.mouse.move(650, 300)
    page.mouse.down()
    page.mouse.move(740, 350, steps=8)
    page.mouse.up()
    frames(page, 8)
    check("L06 real pointer dragging changes the camera", abs(page.evaluate("() => __galaxy.camera().tTheta") - theta) > 0.1)

    page.locator("#legendBtn").click()
    search(page, "Moon", "moon")
    settle(page, "EARTHMOON_IDLE", "focusedEarthMoon", "moon")
    check("L10 open legend yields to the information panel", page.locator("#legend").evaluate("e => getComputedStyle(e).opacity") == "0")
    for width, height in [(1280, 800), (1920, 1080), (390, 844), (320, 568), (844, 390)]:
        page.set_viewport_size({"width": width, "height": height})
        layout_shot(page, "moon-info-%dx%d" % (width, height), focused="moon")
        check("L07 quality controls remain available at %dx%d" % (width, height), page.locator('[data-quality="auto"]').is_visible())
        if width <= 780 or height <= 500:
            gap = page.locator("#bar").evaluate("e => document.getElementById('sidebar').getBoundingClientRect().top-e.getBoundingClientRect().bottom")
            check("L08 toolbar follows measured dock height at %dx%d" % (width, height), 8 <= gap <= 12, gap)
            results(page, "\u661f")
            layout_shot(page, "search-%dx%d" % (width, height))
            page.locator("#objectSearch").press("Escape")
            frames(page, 3)
            page.locator("#closeInfo").click()
            layout_shot(page, "moon-%dx%d" % (width, height), focused="moon")
            page.locator('[data-mrate="8"]').click()
            check("L11 offscreen compact controls are reachable at %dx%d" % (width, height), page.evaluate("() => __galaxy.moonControls().rate") == 8)
            page.locator('[data-mrate="1"]').click()
            page.locator("#moonBar").evaluate("e => { e.scrollLeft=0; }")
            page.evaluate("() => __galaxy.focusEarthMoonPart('moon')")
            settle(page, "EARTHMOON_IDLE", "focusedEarthMoon", "moon")
    page.set_viewport_size({"width": 1280, "height": 800})
    search(page, "Earth", "earth")
    settle(page, "PLANET_FOCUS")
    for width, height in [(1280, 800), (390, 844), (320, 568), (844, 390)]:
        page.set_viewport_size({"width": width, "height": height})
        layout_shot(page, "earth-info-%dx%d" % (width, height), focused="earth")
        page.locator("#infoScroll").evaluate("e => { e.scrollTop=e.scrollHeight; }")
        check("L12 information can scroll without hiding its close button at %dx%d" % (width, height), page.locator("#closeInfo").evaluate("e => {const r=e.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===e;}"))
        page.locator("#infoScroll").evaluate("e => { e.scrollTop=0; }")
    ctx.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["search", "quality", "layout"])
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-gpu-sandbox",
        ])
        try:
            for name, test in [("search", test_search), ("quality", test_quality), ("layout", test_layout)]:
                if args.only is None or args.only == name:
                    test(browser, errors)
        finally:
            browser.close()
    check("E01 no page or console errors", not errors, errors[:5])
    print("\n%d passed, %d failed" % (passed, failed), flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
