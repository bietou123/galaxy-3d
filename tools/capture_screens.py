"""P8 截图与性能采样（docs/prompt-100k 第九卷 9.10、第十卷 10.5）。

对每个层级冷启动一次页面（走真实深链接路径），等相机补间结束后截图，
存档到 docs/screenshots/，并在三种画质下各采样一段帧时间。

无头 Chromium 使用 SwiftShader 软件渲染，帧时间只能做相对比较，
不代表真实显卡上的 FPS —— 第九卷 9.12 的性能预算仍需在真机核对。

用法：
    pip install playwright
    python -m playwright install chromium
    python tools/capture_screens.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
OUT = ROOT / "docs" / "screenshots"

# (文件名, 深链接, 结束状态, 说明, 进入后再按的键, 进入后再点的控件)
# 银河三个视角没有对应的哈希（只有 #galaxy/r<半径>），所以冷启动到默认
# 视角后按 1 / 2 / 3 切俯视、侧视、盘内。
SHOTS = [
    ("01-localgroup", "#localgroup", "LOCALGROUP_IDLE", "本星系群", None, None),
    ("02-galaxy-top", "", "GALAXY_IDLE", "银河俯视", "1", None),
    ("03-galaxy-edge", "", "GALAXY_IDLE", "银河侧视光带", "2", None),
    ("04-galaxy-inside", "", "GALAXY_IDLE", "银河盘内", "3", None),
    ("05-star-alphacen", "#star/alphacen", "STAR_IDLE", "南门二三合星", None, None),
    ("06-solar", "#solar", "SOLAR_IDLE", "太阳系真实轨道间距", None, None),
    ("07-body-earth", "#body/earth", "PLANET_FOCUS", "地球聚焦", None, None),
    ("08-earthmoon", "#earthmoon", "EARTHMOON_IDLE", "地月系", None, None),
    ("11-moon", "#earthmoon/moon", "EARTHMOON_IDLE", "月球聚焦", None, None),
    ("12-solar-inner", "#solar", "SOLAR_IDLE", "内行星真实轨道间距", None,
     '[data-solar-view="inner"]'),
]

# 关掉 CSS 过渡，并隐藏启动提示条：toast 会在开屏 1.6 秒后出现、
# 停留 4 秒再淡出，截图时常常正好拍到半透明的一行字压在银河上。
NO_ANIM = ("*{transition:none !important;animation:none !important;}"
           "#toast{display:none !important;}")


def boot(page, suffix):
    page.goto("about:blank")
    page.goto(PAGE.as_uri() + "?quality=high" + suffix)
    page.wait_for_selector("#boot.gone", state="attached", timeout=60000)


def settle(page, state_name, tries=120, step_ms=250):
    """轮询探针：转到目标状态、补间结束、相机接受输入。"""
    for _ in range(tries):
        d = page.evaluate("() => window.__galaxy.dump()")
        cam = page.evaluate("() => window.__galaxy.camera()")
        if (d["state"] == state_name and d["camEnabled"]
                and not cam["tweenActive"]
                and not page.evaluate("() => window.__galaxy.navWant()")):
            return True
        page.wait_for_timeout(step_ms)
    return False


def sample_frames(page, frames=40):
    """用 rAF 采样帧间隔，返回中位数与均值（毫秒）。"""
    return page.evaluate("""
      (n) => new Promise((resolve) => {
        const ts = [];
        let prev = 0;
        function tick(t) {
          if (prev) ts.push(t - prev);
          prev = t;
          if (ts.length >= n) {
            ts.sort((a, b) => a - b);
            resolve({
              median: ts[Math.floor(ts.length / 2)],
              mean: ts.reduce((a, b) => a + b, 0) / ts.length,
              worst: ts[ts.length - 1]
            });
            return;
          }
          requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
      })
    """, frames)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--disable-gpu-sandbox",
        ])
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console: " + m.text)
                if m.type == "error" else None)
        page.add_init_script(
            "document.addEventListener('DOMContentLoaded', () => {"
            " const s = document.createElement('style');"
            " s.textContent = " + repr(NO_ANIM) + ";"
            " document.head.appendChild(s); });")

        for name, suffix, state_name, desc, key, control in SHOTS:
            boot(page, suffix)
            ok = settle(page, state_name)
            if key:
                page.keyboard.press(key)
                # 切视角会起一段相机补间，等它落定再截
                ok = settle(page, state_name) and ok
            if control:
                page.locator(control).click()
                ok = settle(page, state_name) and ok
            if not ok:
                errors.append(name + ": scene did not settle")
            page.wait_for_timeout(700)
            path = OUT / (name + ".jpg")
            page.screenshot(path=str(path), type="jpeg", quality=90)
            kb = path.stat().st_size // 1024
            print("%s %-18s %-16s %4d KB%s  %s"
                  % ("OK  " if ok else "WARN", name, state_name, kb,
                     "  !超过 300KB" if kb > 300 else "", desc))

        page.set_viewport_size({"width": 390, "height": 844})
        for name, suffix, state_name in [
            ("09-mobile-galaxy", "", "GALAXY_IDLE"),
            ("10-mobile-moon", "#earthmoon/moon", "EARTHMOON_IDLE"),
        ]:
            boot(page, suffix)
            ok = settle(page, state_name)
            if not ok:
                errors.append(name + ": scene did not settle")
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT / (name + ".jpg")), type="jpeg", quality=90)
            print("%s %s" % ("OK  " if ok else "WARN", name))
        page.set_viewport_size({"width": 1280, "height": 720})

        # 三档画质帧时间：同一银河视角，各采样 40 帧
        if "--shots-only" not in sys.argv:
            print("")
            print("帧时间采样（SwiftShader 软件渲染，仅作相对参考）")
            for level in ("high", "mid", "low"):
                page.goto("about:blank")
                page.goto(PAGE.as_uri() + "?quality=" + level)
                page.wait_for_selector("#boot.gone", state="attached",
                                       timeout=60000)
                page.wait_for_timeout(1200)
                st = sample_frames(page)
                print("  %-5s 中位 %6.1f ms  均值 %6.1f ms  最差 %6.1f ms  ≈%4.1f FPS"
                      % (level, st["median"], st["mean"], st["worst"],
                         1000.0 / st["median"] if st["median"] else 0))

        browser.close()

    if errors:
        print("")
        print("页面错误：")
        for e in errors[:5]:
            print("  " + e)
        return 1
    print("")
    print("截图与采样完成，无页面错误。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
