"""P7 验收：交互与画质打磨（docs/prompt-100k 第七卷 7.16、第五卷 5.7）。

本脚本验证的是「能在界面上真的操作到」的那部分：
  - 面包屑逐级导航与跨层直跳的拆解；
  - 顶栏/侧栏双份显示开关同步，暗尘带独立于画质档位；
  - 演示模式可进入、可被任意输入打断、不改变层级；
  - 手动改地址栏能跳转；prefers-reduced-motion 下动画显著削弱；
  - 地月系工具栏：月相跳转、独立倍率 0.25×–8×、跟随月球、看背面、放大月球；
  - 移动端单指旋转、双指缩放、双指平移，且没有页面滚动穿透。

无头环境（SwiftShader）里 requestAnimationFrame 很慢，转场与补间按帧推进，
所以等待一律轮询探针状态，不写死 sleep 时长。

用法：
    pip install playwright
    python -m playwright install chromium
    python tools/acceptance_p7.py
"""

import math
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"

# Windows 控制台默认 GBK，验收名里有「›」「×」这类符号会直接抛
# UnicodeEncodeError 打断整轮验收；无法编码的字符降级成 ? 即可。
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:      # Python < 3.7
    pass

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


def crumbs(page):
    return page.evaluate("() => window.__galaxy.breadcrumb()")


def toggles(page):
    return page.evaluate("() => window.__galaxy.toggles()")


def scale(page):
    return page.evaluate("() => window.__galaxy.scale()")


def controls(page):
    return page.evaluate("() => window.__galaxy.moonControls()")


def camera(page):
    return page.evaluate("() => window.__galaxy.camera()")


def info(page):
    return page.evaluate("() => window.__galaxy.earthMoonInfo()")


def wait_for(page, predicate, tries=160, step_ms=500):
    d = dump(page)
    for _ in range(tries):
        if predicate(d):
            return True, d
        page.wait_for_timeout(step_ms)
        d = dump(page)
    return predicate(d), d


def wait_layer(page, states, layer, alpha, tries=200):
    def pred(d):
        return d["state"] in states and d["layers"][layer] > alpha
    return wait_for(page, pred, tries=tries)


def boot(page, hash_suffix=""):
    page.goto("about:blank")
    page.goto(PAGE.as_uri() + hash_suffix)
    page.wait_for_selector("#boot.gone", state="attached", timeout=90000)
    page.wait_for_timeout(600)


def crumb_layers(page):
    return [c["layer"] for c in crumbs(page)]


def cur_crumb(page):
    for c in crumbs(page):
        if c["cur"]:
            return c["layer"], c["label"]
    return None, None


def wrap_pi(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


# 合成触控事件。真实触屏会先产生 pointer 事件再产生 touch 事件，无头环境里
# 没有触屏，只能分开合成：单指旋转走 PointerEvent（拖动逻辑在 pointermove），
# 双指缩放/平移走 TouchEvent（两个触点由 touches 数组表达）。
SYNTH_POINTER = """(pts) => {
  var c = document.getElementById('gl');
  for (var i = 0; i < pts.length; i++) {
    var p = pts[i];
    c.dispatchEvent(new PointerEvent(p[0], {
      pointerId: 11, pointerType: 'touch', isPrimary: true,
      clientX: p[1], clientY: p[2], button: 0, buttons: 1,
      bubbles: true, cancelable: true
    }));
  }
  return true;
}"""

SYNTH_TOUCH = """(arg) => {
  var c = document.getElementById('gl');
  var pts = arg[1].map(function (p) {
    return new Touch({ identifier: p[2], target: c,
      clientX: p[0], clientY: p[1], pageX: p[0], pageY: p[1],
      radiusX: 8, radiusY: 8, force: 1 });
  });
  c.dispatchEvent(new TouchEvent(arg[0], {
    touches: pts, targetTouches: pts, changedTouches: pts,
    bubbles: true, cancelable: true
  }));
  return true;
}"""


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--disable-gpu-sandbox",
        ])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console: " + m.text)
                if m.type == "error" else None)

        # ================= 面包屑、显示开关、画质 =================
        boot(page)

        c = crumbs(page)
        check("B01 银河层面包屑是「本星系群 › 银河系」",
              crumb_layers(page) == ["group", "galaxy"], c)
        check("B01 当前层标记在银河系上",
              cur_crumb(page) == ("galaxy", "银河系"), cur_crumb(page))

        t = toggles(page)
        check("B02 开关初值：轨道/标签/暗尘/比例尺全开",
              all(t[k] is True for k in
                  ("orbits", "labels", "dust", "scale")), t)
        check("B02 顶栏与侧栏两份开关同源（初值一致）",
              t["chkOrbit"] == t["chkOrbit2"] is True
              and t["chkName"] == t["chkName2"] is True, t)

        t = page.evaluate("() => window.__galaxy.setToggle('orbits', false)")
        check("B03 关轨道线后两份复选框一起关",
              t["orbits"] is False and t["chkOrbit"] is False
              and t["chkOrbit2"] is False, t)
        t = page.evaluate("() => window.__galaxy.setToggle('labels', false)")
        check("B03 关标签后两份复选框一起关",
              t["labels"] is False and t["chkName"] is False
              and t["chkName2"] is False, t)
        t = page.evaluate("() => window.__galaxy.setToggle('orbits', true)")
        t = page.evaluate("() => window.__galaxy.setToggle('labels', true)")
        check("B03 重新打开后恢复同步",
              t["orbits"] is True and t["labels"] is True
              and t["chkOrbit2"] is True and t["chkName2"] is True, t)

        # 比例尺：关掉之后不仅收起，updateScale 也应停止每帧重算
        page.evaluate("() => window.__galaxy.setToggle('scale', false)")
        hidden, _ = wait_for(page, lambda s: scale(page)["show"] is False, tries=20)
        check("B04 关掉比例尺后比例尺收起", hidden, scale(page))
        page.evaluate("() => window.__galaxy.setToggle('scale', true)")
        shown, _ = wait_for(page, lambda s: scale(page)["show"] is True, tries=20)
        s = scale(page)
        check("B04 重新打开后比例尺恢复显示", shown, s)
        check("B04 银河层比例尺是光年制且条形有长度",
              s["mode"] == "galaxy" and "光年" in s["label"] and s["barPx"] >= 24, s)

        # 暗尘带：用户开关与画质档位是两个独立条件
        t = page.evaluate("() => window.__galaxy.setQuality('high')")
        check("B05 手动切到高画质后按档位允许画暗尘",
              t["quality"] == "high" and t["dustDrawn"] is True, t)
        t = page.evaluate("() => window.__galaxy.setToggle('dust', false)")
        check("B05 手动关暗尘：高画质下也不画",
              t["dust"] is False and t["dustDrawn"] is False, t)
        t = page.evaluate("() => window.__galaxy.setQuality('low')")
        check("B05 低画质强制不画暗尘",
              t["quality"] == "low" and t["dustDrawn"] is False, t)
        check("B05 低画质没有改掉用户的暗尘开关（仍是用户手动关的状态）",
              t["dust"] is False, t)
        t = page.evaluate("() => window.__galaxy.setToggle('dust', true)")
        check("B05 低画质下用户打开也不画（档位优先）",
              t["dust"] is True and t["dustDrawn"] is False, t)
        t = page.evaluate("() => window.__galaxy.setQuality('high')")
        check("B05 回到高画质后用户开着就画",
              t["dustDrawn"] is True, t)
        qbtns = page.evaluate("""() => Array.from(
          document.querySelectorAll('[data-quality]')).map(function (b) {
            return { level: b.dataset.quality, on: b.classList.contains('on') };
          })""")
        check("B05 画质按钮高亮与当前档位一致",
              [b["level"] for b in qbtns if b["on"]] == ["high"], qbtns)

        # ================= 演示模式（7.16 第 9 条） =================
        d0 = dump(page)
        demo = page.evaluate("() => window.__galaxy.setDemo(true)")
        check("B06 演示模式可进入，界面隐藏并给出退出提示",
              demo["active"] is True and demo["hint"] is True
              and "任意操作退出" in demo["hintText"], demo)
        check("B06 普通动效下会排巡游脚本",
              demo["tour"] is True and demo["stepCount"] >= 3, demo)
        page.wait_for_timeout(800)
        d = dump(page)
        check("B06 演示开始后层级状态不变",
              d["state"] == d0["state"], (d0["state"], d["state"]))

        page.mouse.move(640, 400)
        page.mouse.wheel(0, 120)
        page.wait_for_timeout(400)
        demo = page.evaluate("() => window.__galaxy.demo()")
        d = dump(page)
        check("B06 滚轮打断演示，且滚轮本身不再缩放",
              demo["active"] is False and demo["tour"] is False
              and d["state"] == d0["state"]
              and abs(d["tRadius"] - d0["tRadius"]) < 1e-6,
              (demo, d["tRadius"], d0["tRadius"]))

        page.evaluate("() => window.__galaxy.setDemo(true)")
        page.keyboard.press("a")
        page.wait_for_timeout(400)
        demo = page.evaluate("() => window.__galaxy.demo()")
        d = dump(page)
        check("B06 任意按键打断演示，且该键的快捷键不生效",
              demo["active"] is False and d["state"] == d0["state"], (demo, d))

        page.keyboard.press("h")
        page.wait_for_timeout(300)
        on = page.evaluate("() => window.__galaxy.demo()['active']")
        page.keyboard.press("h")
        page.wait_for_timeout(300)
        off = page.evaluate("() => window.__galaxy.demo()['active']")
        check("B06 H 键开关演示模式", on is True and off is False, (on, off))

        # ================= 地址栏手动跳转（7.16 第 11 条） =================
        page.evaluate("() => { location.hash = '#solar'; }")
        ok, d = wait_layer(page, ("SOLAR_IDLE",), "solar", 0.98)
        check("B07 地址栏改成 #solar 会进入太阳系", ok, d["state"])
        check("B07 面包屑跟着补出太阳系一级",
              crumb_layers(page) == ["group", "galaxy", "solar"],
              crumb_layers(page))
        check("B07 地址栏哈希与当前层级一致",
              page.evaluate("() => location.hash") == "#solar",
              page.evaluate("() => location.hash"))

        page.evaluate("() => window.__galaxy.goToLayer('galaxy')")
        ok, d = wait_layer(page, ("GALAXY_IDLE",), "galaxy", 0.98)
        check("B08 面包屑点银河系退回银河层", ok, d["state"])
        check("B08 退回后面包屑只剩两级",
              crumb_layers(page) == ["group", "galaxy"], crumb_layers(page))

        page.evaluate("() => { location.hash = '#star/sirius'; }")
        ok, d = wait_layer(page, ("STAR_IDLE",), "star", 0.98)
        check("B09 地址栏改成 #star/sirius 会进入天狼星系统", ok, d["state"])
        check("B09 面包屑末级是天狼星（恒星系统是银河的旁支，不挂太阳系下）",
              cur_crumb(page) == ("star", "天狼星"), cur_crumb(page))

        # 跨层直跳必须拆成多步：恒星 → 银河 → 太阳系 → 地月系
        page.evaluate("() => window.__galaxy.goToLayer('earthmoon')")
        ok, d = wait_layer(page, ("EARTHMOON_IDLE",), "earthmoon", 0.98,
                           tries=260)
        check("B10 从恒星系统直点地月系，逐级转场后抵达", ok,
              (d["state"], d["layers"]))
        check("B10 地月系面包屑是完整四级链",
              crumb_layers(page) ==
              ["group", "galaxy", "solar", "earthmoon"], crumb_layers(page))
        # 状态到位与相机补间不是同一个时刻：状态回调按墙钟设置，补间按帧推进，
        # updateNav 在补间期间不动，所以 navWant 会晚一点才被清掉。
        settled, _ = wait_for(
            page, lambda s: page.evaluate(
                "() => window.__galaxy.navWant()") is None, tries=20)
        check("B10 转场推进完毕后没有残留的导航意图", settled,
              page.evaluate("() => window.__galaxy.navWant()"))

        page.evaluate("() => window.__galaxy.goToLayer('galaxy')")
        ok, d = wait_layer(page, ("GALAXY_IDLE",), "galaxy", 0.98, tries=260)
        check("B11 从地月系沿面包屑一路退回银河层", ok, d["state"])
        check("B11 其余各层已淡出",
              d["layers"]["solar"] < 0.02 and d["layers"]["earthmoon"] < 0.02,
              d["layers"])

        # ================= 移动端手势（7.16 第 12 条） =================
        # 相机补间期间每帧会把 tTheta / tPhi / tRadius 写回补间值，手势会被
        # 立刻覆盖——这不是手势失效，而是退层后补间还在收尾。先等它跑完。
        idle, _ = wait_for(page, lambda s: camera(page)["tweenActive"] is False,
                           tries=40)
        check("B12 退层后相机补间已结束（手势测试的前提）", idle,
              camera(page)["tweenActive"])

        cam0 = camera(page)
        page.evaluate(SYNTH_POINTER, [
            ["pointerdown", 600, 400],
            ["pointermove", 612, 404],
            ["pointermove", 624, 408],
            ["pointermove", 636, 412],
            ["pointermove", 648, 416],
            ["pointerup", 660, 420],
        ])
        page.wait_for_timeout(300)
        cam1 = camera(page)
        check("B12 单指拖动可以旋转（方位角改变）",
              abs(cam1["tTheta"] - cam0["tTheta"]) > 0.15,
              (cam0["tTheta"], cam1["tTheta"]))
        check("B12 单指旋转也能改变俯仰角，不限制在水平方向",
              abs(cam1["tPhi"] - cam0["tPhi"]) > 0.005,
              (cam0["tPhi"], cam1["tPhi"]))

        # 双指张开 = 拉近（半径变小）
        cam0 = camera(page)
        page.evaluate(SYNTH_TOUCH, ["touchstart",
                                    [[500, 400, 1], [700, 400, 2]]])
        page.evaluate(SYNTH_TOUCH, ["touchmove",
                                    [[450, 400, 1], [750, 400, 2]]])
        page.wait_for_timeout(200)
        cam1 = camera(page)
        page.evaluate(SYNTH_TOUCH, ["touchend", []])
        check("B12 双指张开拉近相机",
              cam1["tRadius"] < cam0["tRadius"] - 1,
              (cam0["tRadius"], cam1["tRadius"]))

        # 双指同向平移 = 移动注视点
        cam0 = camera(page)
        page.evaluate(SYNTH_TOUCH, ["touchstart",
                                    [[500, 400, 1], [700, 400, 2]]])
        page.evaluate(SYNTH_TOUCH, ["touchmove",
                                    [[530, 430, 1], [730, 430, 2]]])
        page.wait_for_timeout(200)
        cam1 = camera(page)
        page.evaluate(SYNTH_TOUCH, ["touchend", []])
        moved = dist(cam0["tTarget"], cam1["tTarget"])
        check("B12 双指同向平移会移动注视点", moved > 0.01,
              (cam0["tTarget"], cam1["tTarget"]))
        check("B12 平移时半径不变（平移与缩放分开）",
              abs(cam1["tRadius"] - cam0["tRadius"]) < 0.1,
              (cam0["tRadius"], cam1["tRadius"]))

        scroll = page.evaluate("""() => ({
          touchAction: getComputedStyle(document.getElementById('gl')).touchAction,
          scrollY: window.scrollY,
          docH: document.scrollingElement.scrollHeight,
          winH: window.innerHeight
        })""")
        check("B12 画布声明 touch-action:none，手势不穿透成页面滚动",
              scroll["touchAction"] == "none" and scroll["scrollY"] == 0
              and scroll["docH"] <= scroll["winH"] + 1, scroll)

        # ================= 地月系工具栏（5.7） =================
        boot(page, "#earthmoon")
        ok, d = wait_layer(page, ("EARTHMOON_IDLE",), "earthmoon", 0.98)
        check("M01 冷启动 #earthmoon 抵达地月系", ok, d["state"])

        m = controls(page)
        check("M01 工具栏在地月系层显示", m["barShown"] is True, m)
        check("M01 默认倍率 1×，只有 1× 按钮亮起",
              m["rate"] == 1 and m["rateButtonsOn"] == [1], m)
        check("M01 默认真实比例，说明文字是「真实比例」",
              m["zoom"] is False and m["note"] == "真实比例", m)
        check("M01 没有跟随月球时「看背面」不可用",
              m["followed"] is None and m["farEnabled"] is False, m)

        # 月相跳转：四个整数相位
        cases = [
            ("new", "新月", 0.0, None),
            ("first", "上弦月", 0.5, True),
            ("full", "满月", 1.0, None),
            ("last", "下弦月", 0.5, False),
        ]
        for key, want_phase, want_frac, want_waxing in cases:
            # 同一次调用取月相与按钮，避免自然公转在两次采样间改变相位。
            snapshot = page.evaluate("""(k) => ({
              info: window.__galaxy.jumpMoonPhase(k),
              controls: window.__galaxy.moonControls()
            })""", key)
            e = snapshot["info"]
            m = snapshot["controls"]
            ok = e is not None and e["phase"] == want_phase
            if want_waxing is not None:
                ok = ok and e["waxing"] is want_waxing
            ok = ok and abs(e["frac"] - want_frac) < 0.02
            ok = ok and m["phaseButtonsOn"] == [key]
            check("M02 跳到%s：相位、亮比例、按钮高亮一致" % want_phase,
                  ok, (e and (e["phase"], e["frac"], e["waxing"]), m))

        e = page.evaluate("() => window.__galaxy.jumpMoonPhase('nope')")
        check("M02 非法月相名被忽略", e is None, e)

        # 独立倍率：按钮 + 真正改变公转速度
        ok = page.evaluate("() => window.__galaxy.setMoonRate(4)")
        m = controls(page)
        check("M03 切到 4× 成功，按钮高亮跟上",
              ok is True and m["rate"] == 4 and m["rateButtonsOn"] == [4], m)
        ok = page.evaluate("() => window.__galaxy.setMoonRate(100)")
        check("M03 越界倍率被拒绝且不改变当前值",
              ok is False and controls(page)["rate"] == 4, controls(page))

        page.evaluate("() => window.__galaxy.jumpMoonPhase('new')")
        page.evaluate("() => window.__galaxy.setMoonRate(0.25)")
        a0 = info(page)["angle"]
        page.wait_for_timeout(3000)
        a1 = info(page)["angle"]
        slow = abs(wrap_pi(a1 - a0))

        page.evaluate("() => window.__galaxy.setMoonRate(8)")
        a2 = info(page)["angle"]
        page.wait_for_timeout(3000)
        a3 = info(page)["angle"]
        fast = abs(wrap_pi(a3 - a2))
        check("M03 8× 比 0.25× 快一个数量级以上（倍率真的接进时间积分）",
              fast > 0.15 and fast > 10 * slow, (slow, fast))

        # 跟随月球 → 看背面
        page.evaluate("() => window.__galaxy.setMoonRate(1)")
        d = page.evaluate("() => window.__galaxy.followMoon()")
        check("M04 跟随月球：探针记为聚焦月球",
              d is not None and d["focusedEarthMoon"] == "moon", d)
        m = controls(page)
        check("M04 跟随按钮变成「停止跟随」，「看背面」解锁",
              m["followLabel"] == "停止跟随" and m["farEnabled"] is True, m)
        check("M04 深链接同步到 #earthmoon/moon",
              page.evaluate("() => location.hash") == "#earthmoon/moon",
              page.evaluate("() => location.hash"))
        p = page.evaluate("""() => ({
          show: document.getElementById('info').classList.contains('show'),
          zh: document.getElementById('infoZh').textContent })""")
        check("M04 跟随同时打开月球资料面板",
              p["show"] is True and p["zh"] == "月球", p)

        ok = page.evaluate("() => window.__galaxy.moonFarSide()")
        check("M05 「看背面」在跟随状态下可用", ok is True, ok)
        page.wait_for_timeout(2200)        # 等聚焦补间先跑完，再由逐帧跟踪接管
        e = info(page)
        c = camera(page)
        rel = [e["moonPos"][i] - e["earthPos"][i] for i in range(3)]
        off = [c["eye"][i] - e["moonPos"][i] for i in range(3)]
        check("M05 相机站在月球背对地球的一侧",
              dot(rel, off) > 0, (rel, off))
        check("M05 相机比月球更远离地球",
              dist(c["eye"], e["earthPos"]) > dist(e["moonPos"], e["earthPos"]),
              (dist(c["eye"], e["earthPos"]),
               dist(e["moonPos"], e["earthPos"])))
        check("M05 注视点锁在月球上",
              dist(c["target"], e["moonPos"]) < 0.6,
              (c["target"], e["moonPos"]))
        first_eye = list(c["eye"])
        page.wait_for_timeout(3000)
        e2 = info(page)
        c2 = camera(page)
        rel2 = [e2["moonPos"][i] - e2["earthPos"][i] for i in range(3)]
        off2 = [c2["eye"][i] - e2["moonPos"][i] for i in range(3)]
        check("M05 月球公转后相机继续跟着绕到背面（不是一次性的机位）",
              dot(rel2, off2) > 0 and dist(first_eye, c2["eye"]) > 0.05,
              (dot(rel2, off2), dist(first_eye, c2["eye"])))

        # 用户自己拖动就取消「看背面」，否则松手后会被立刻拉回去
        page.mouse.move(640, 400)
        page.mouse.down()
        page.mouse.move(700, 430, steps=6)
        page.mouse.up()
        page.wait_for_timeout(400)
        check("M05 手动拖动旋转会取消「看背面」",
              controls(page)["farMode"] is False, controls(page))

        # 放大月球 3×：只改视觉半径，真实比例数据不动
        ok = page.evaluate("() => window.__galaxy.setMoonZoom(true)")
        m = controls(page)
        e = info(page)
        check("M06 放大月球后视觉半径约为 0.273 × 3",
              ok is True and abs(m["moonVisualR"] - 0.819) < 0.002, m)
        check("M06 界面注明「月球已放大 3 倍」",
              m["note"] == "月球已放大 3 倍", m)
        check("M06 真实半径数据不变（moonR 仍是 0.273）",
              abs(e["moonR"] - 0.273) < 1e-9, e["moonR"])
        page.evaluate("() => window.__galaxy.setMoonZoom(false)")
        m = controls(page)
        e = info(page)
        check("M06 恢复真实比例后视觉半径回到 0.273",
              abs(m["moonVisualR"] - 0.273) < 0.002
              and m["note"] == "真实比例", m)
        check("M06 恢复后真实半径仍与数据一致",
              abs(e["moonR"] - 0.273) < 1e-9, e["moonR"])

        # 停止跟随：回到默认构图，深度链接退回这一层
        page.evaluate("() => window.__galaxy.followMoon()")
        m = controls(page)
        check("M07 再点一次跟随按钮会取消跟随",
              m["followed"] is None and m["farMode"] is False
              and m["farEnabled"] is False, m)
        check("M07 取消聚焦后深链接回到 #earthmoon",
              page.evaluate("() => location.hash") == "#earthmoon",
              page.evaluate("() => location.hash"))

        # 退出再进：倍率、放大、看背面都回到出厂状态
        page.evaluate("() => window.__galaxy.setMoonRate(8)")
        page.evaluate("() => window.__galaxy.setMoonZoom(true)")
        page.evaluate("() => window.__galaxy.exitEarthMoon()")
        ok, d = wait_for(page, lambda s: (
            s["state"] in ("SOLAR_IDLE", "PLANET_FOCUS")
            and s["layers"]["earthmoon"] < 0.02), tries=120)
        check("M08 退出地月系回到太阳系一侧", ok, d["state"])
        page.evaluate("() => window.__galaxy.enterEarthMoon()")
        ok, d = wait_layer(page, ("EARTHMOON_IDLE",), "earthmoon", 0.98)
        m = controls(page)
        check("M08 重新进入后倍率复位为 1×", ok and m["rate"] == 1, m)
        check("M08 重新进入后月球回到真实比例",
              m["zoom"] is False and abs(m["moonVisualR"] - 0.273) < 0.002, m)
        check("M08 重新进入后不残留跟随与看背面状态",
              m["followed"] is None and m["farMode"] is False, m)

        ctx.close()

        # ================= 减少动效（7.16 第 10 条） =================
        rm_errors = []
        ctx2 = browser.new_context(viewport={"width": 1280, "height": 800},
                                   reduced_motion="reduce")
        page2 = ctx2.new_page()
        page2.on("pageerror", lambda e: rm_errors.append("pageerror: " + str(e)))
        page2.on("console", lambda m: rm_errors.append("console: " + m.text)
                 if m.type == "error" else None)
        page2.goto(PAGE.as_uri())
        page2.wait_for_selector("#boot.gone", state="attached", timeout=90000)
        page2.wait_for_timeout(600)

        demo = page2.evaluate("() => window.__galaxy.demo()")
        check("R01 prefers-reduced-motion 被识别", demo["reduced"] is True, demo)
        demo = page2.evaluate("() => window.__galaxy.setDemo(true)")
        check("R01 减少动效时不自动巡游，只隐藏界面",
              demo["active"] is True and demo["tour"] is False
              and "禁用自动巡游" in demo["hintText"], demo)
        td = page2.evaluate("""() => {
          var a = getComputedStyle(document.getElementById('crumbs'))
            .transitionDuration;
          var b = getComputedStyle(document.getElementById('veil'))
            .transitionDuration;
          return [a, b];
        }""")
        def secs(v):
            return float(v.replace("s", "").split(",")[0]) if v else 1.0
        check("R01 界面过渡被压到接近瞬时",
              all(secs(v) < 0.01 for v in td), td)
        page2.evaluate("() => window.__galaxy.setDemo(false)")
        ctx2.close()

        check("无页面错误", not errors and not rm_errors,
              "; ".join((errors + rm_errors)[:3]))
        browser.close()

    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
