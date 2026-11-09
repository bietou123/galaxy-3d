"""P3 验收：下钻判定与返回栈（docs/prompt-100k 第十卷 I04 / I05 / I06）。

这里只验证"放大会不会乱进太阳系"和"退出后相机还在不在原处"两件事。
无头环境（SwiftShader）里 requestAnimationFrame 很慢，补间按每帧 dt 推进，
所以所有等待都改成轮询探针状态，不写死 sleep 时长。

用法：
    pip install playwright
    python -m playwright install chromium
    python tools/acceptance_p3.py
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
        print("FAIL  " + name + ("  (" + str(detail) + ")" if detail else ""))


def dump(page):
    return page.evaluate("() => window.__galaxy.dump()")


def wait_for(page, predicate, tries=80, step_ms=500):
    """反复读取探针状态，直到条件成立或超时。"""
    d = dump(page)
    for _ in range(tries):
        if predicate(d):
            return True, d
        page.wait_for_timeout(step_ms)
        d = dump(page)
    return predicate(d), d


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--disable-gpu-sandbox",
        ])
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console: " + m.text)
                if m.type == "error" else None)

        page.goto(PAGE.as_uri())
        page.wait_for_selector("#boot.gone", state="attached", timeout=60000)
        page.wait_for_timeout(800)

        probe = page.evaluate("() => window.__galaxy ? typeof window.__galaxy.dump : 'missing'")
        check("探针 window.__galaxy 可用", probe == "function", probe)

        # ---- I04 未对准空白盘面，缩到最小半径不下钻 ----
        page.evaluate("() => window.__galaxy.aim(0, 0, 0, 250)")
        d = dump(page)
        sun = d["sun"]
        check("I04 太阳标记不在屏幕中心", sun is None or sun["dist"] > 100, sun)
        entered = page.evaluate("() => window.__galaxy.tryDive()")
        d = dump(page)
        check("I04 tryDive 返回 false", entered is False, entered)
        check("I04 仍停留在银河层", d["state"] == "GALAXY_IDLE", d["state"])
        check("I04 相机半径停在最小值", abs(d["tRadius"] - 250) < 1, d["tRadius"])

        # ---- I04b 对准了，但标记还太小，说明用户在更大的盘面上，不该下钻 ----
        d = page.evaluate("() => window.__galaxy.aimAtSun(1400)")
        check("I04b 已对准但标记偏小",
              d["sun"] and d["sun"]["dist"] <= 34 and d["sun"]["sr"] < 70, d["sun"])
        entered = page.evaluate("() => window.__galaxy.tryDive()")
        check("I04b 标记过小时不下钻", entered is False, entered)
        check("I04b 仍停留在银河层", dump(page)["state"] == "GALAXY_IDLE")

        # ---- I05 对准太阳且标记足够大，允许下钻 ----
        home = page.evaluate("() => window.__galaxy.aimAtSun(250)")
        check("I05 太阳标记居中", home["sun"]["dist"] <= 34, home["sun"])
        check("I05 标记屏幕半径不小于 70px", home["sun"]["sr"] >= 70, home["sun"])
        entered = page.evaluate("() => window.__galaxy.tryDive()")
        check("I05 tryDive 返回 true", entered is True, entered)
        check("I05 进入转场状态", dump(page)["state"] == "TRANSITION")

        arrived, d = wait_for(page, lambda s: s["state"] == "SOLAR_IDLE")
        check("I05 抵达太阳系层", arrived, d["state"])

        # ---- I06 Esc 返回，相机回到进入前的位置 ----
        page.keyboard.press("Escape")
        # 补间与阻尼都要收尾，不能只看状态位
        back, d = wait_for(page, lambda s: (
            s["state"] == "GALAXY_IDLE"
            and abs(s["tRadius"] - home["radius"]) < 12
            and max(abs(s["target"][i] - home["target"][i]) for i in range(3)) < 5
        ))
        check("I06 回到银河层", back, d["state"])
        check("I06 相机半径与进入前接近",
              abs(d["tRadius"] - home["radius"]) < 12, d["tRadius"])
        deviation = max(abs(d["target"][i] - home["target"][i]) for i in range(3))
        check("I06 注视点与进入前接近", deviation < 5, (home["target"], d["target"]))

        browser.close()

    check("无页面错误", not errors, "; ".join(errors[:3]))
    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
