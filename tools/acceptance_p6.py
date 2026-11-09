"""P6 验收：地月系（docs/prompt-100k 第五卷 5.5–5.7、第十卷 L 组）。

用户要求「太阳系再放大可以显示地月系」，并且「按真实的银河系来建模」。
本脚本验证地月系这一层是真的比例：地球半径 1、月球 0.273、平均距离 60.3
个地球半径、偏心率 0.0549、潮汐锁定、月相与盈亏方向、近裁剪不切球。

无头环境（SwiftShader）里 requestAnimationFrame 很慢，补间按每帧 dt 推进，
所以等待一律轮询探针状态，不写死 sleep 时长。

用法：
    pip install playwright
    python -m playwright install chromium
    python tools/acceptance_p6.py
"""

import math
import re
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


def info(page):
    return page.evaluate("() => window.__galaxy.earthMoonInfo()")


def wait_for(page, predicate, tries=120, step_ms=500):
    d = dump(page)
    for _ in range(tries):
        if predicate(d):
            return True, d
        page.wait_for_timeout(step_ms)
        d = dump(page)
    return predicate(d), d


def boot(page, hash_suffix=""):
    page.goto("about:blank")
    page.goto(PAGE.as_uri() + hash_suffix)
    page.wait_for_selector("#boot.gone", state="attached", timeout=60000)
    page.wait_for_timeout(600)


def panel(page):
    return page.evaluate("""() => {
      var info = document.getElementById('info');
      return {
        show: info.classList.contains('show'),
        zh: document.getElementById('infoZh').textContent,
        stats: document.getElementById('stats').textContent,
        desc: document.getElementById('desc').textContent
      };
    }""")


def sidebar(page):
    return page.evaluate("""() => Array.from(
      document.querySelectorAll('#bodyList li')).map(function (li) {
        return { id: li.dataset.id, text: li.textContent,
                 active: li.classList.contains('active') };
      })""")


def wrap_pi(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def phase_name(frac, waxing):
    """与 index.html 的 moonPhaseName 保持同一规则。"""
    if frac < 0.04:
        return "新月"
    if frac > 0.96:
        return "满月"
    if abs(frac - 0.5) <= 0.03:
        return "上弦月" if waxing else "下弦月"
    if frac < 0.5:
        return "蛾眉月" if waxing else "残月"
    return "盈凸月" if waxing else "亏凸月"


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

        # ---- L01 冷启动 #earthmoon 一路落到地月系 ----
        boot(page, "#earthmoon")
        ok, d = wait_for(page, lambda s: (
            s["state"] == "EARTHMOON_IDLE" and s["layers"]["earthmoon"] > 0.98))
        check("L01 #earthmoon 冷启动落在地月系层", ok, d)
        check("L01 其余层已淡出",
              d["layers"]["galaxy"] < 0.02 and d["layers"]["solar"] < 0.02,
              d["layers"])
        check("L01 恒星层与地月系互斥",
              d["layers"]["star"] < 0.02 and d["layers"]["localgroup"] < 0.02,
              d["layers"])

        e = info(page)
        check("L01 探针能读到地球与月球", e is not None, e)
        check("L01 真实比例：地球半径 1 世界单位", abs(e["earthR"] - 1.0) < 1e-9,
              e["earthR"])
        check("L02 真实比例：月球半径 = 0.273 地球半径",
              abs(e["moonR"] / e["earthR"] - 0.273) < 0.002, e["moonR"])
        check("L02 真实距离：地月平均距离 = 60.3 个地球半径",
              abs(e["distanceWu"] / e["earthR"] - 60.3) < 0.1, e["distanceWu"])
        check("L02 时间倍率：4 秒 = 1 天", e["daySeconds"] == 4, e["daySeconds"])
        check("L02 地月系近裁剪收紧到 0.05（全局是 0.5）",
              abs(e["near"] - 0.05) < 1e-9, e["near"])
        check("L02 保持 55° 视场",
              page.evaluate("() => Math.round(window.__galaxy.fov() * 180 / Math.PI)") == 55)

        # ---- L02 默认构图：地球与整条月轨同屏 ----
        # 补间刚结束时 cam.radius 还在阻尼追赶目标值，读 tRadius（目标值）
        # 才是这一层的默认视距。无头环境下 rAF 很慢，补间按帧 dt 累加，
        # 而状态切换按墙钟触发，所以状态到位时补间可能还差最后几帧，
        # 这里等目标值落定再读。
        settled, d = wait_for(page, lambda s: abs(s["tRadius"] - 130) < 1.5,
                              tries=40)
        check("L02 默认视距 130 wu", settled, d["tRadius"])
        e = info(page)
        check("L02 地球在画面内",
              e["earthXY"] and 0 < e["earthXY"][0] < 1280
              and 0 < e["earthXY"][1] < 800, e["earthXY"])
        check("L02 月球在画面内（默认构图不把月球切在屏幕外）",
              e["moonXY"] and 0 < e["moonXY"][0] < 1280
              and 0 < e["moonXY"][1] < 800, e["moonXY"])
        check("L02 地球与月球都画出了像素",
              e["earthPx"] >= 2 and e["moonPx"] >= 1,
              (e["earthPx"], e["moonPx"]))
        # 真实半径比是 1 : 0.273（见上面的 earthR / moonR），屏幕上还要叠加
        # 透视：月球可能比地球离相机近 60 wu，近大远小会把比值压到 2 左右。
        # 因此这里只验证「地球明显大于月球且没有画成同一个球」。
        check("L02 屏幕半径比与真实 1 : 0.273 同量级（含透视近大远小）",
              1.8 <= e["earthPx"] / max(e["moonPx"], 0.1) <= 4.6,
              (e["earthPx"], e["moonPx"]))
        sep_px = math.hypot(e["earthXY"][0] - e["moonXY"][0],
                            e["earthXY"][1] - e["moonXY"][1])
        check("L02 地月分开足够远，不是叠在一起", sep_px > 100, sep_px)

        # ---- L03 轨道：偏心率与倾角 ----
        r_true = e["distanceWu"] * (1 - 0.0549 * math.cos(e["angle"]))
        check("L03 地月距随偏心率 0.0549 变化",
              abs(e["sepWu"] - r_true) < 0.15, (e["sepWu"], r_true))
        check("L03 月轨在 57.0–63.6 wu 之间",
              57.0 <= e["sepWu"] <= 63.6, e["sepWu"])
        # 轨道面绕 x 轴倾斜 5.14°：y 分量应为 sep·sin(角度)·sin(倾角)。
        # 不直接断言 y ≠ 0 —— 采样恰好落在交点附近时 y 自然接近 0。
        dy = e["moonPos"][1] - e["earthPos"][1]
        dy_want = e["sepWu"] * math.sin(e["angle"]) * math.sin(math.radians(5.14))
        check("L03 月轨相对黄道倾斜 5.14°（y 分量符合倾角公式）",
              abs(dy - dy_want) < 0.3, (dy, round(dy_want, 3)))

        # ---- L04 潮汐锁定：月面朝向始终指向地球 ----
        samples = []
        for _ in range(4):
            samples.append(info(page))
            page.wait_for_timeout(3000)
        bad = []
        for s in samples:
            dx = s["moonPos"][0] - s["earthPos"][0]
            dz = s["moonPos"][2] - s["earthPos"][2]
            want = math.atan2(-dx, -dz)
            if abs(wrap_pi(s["moonYaw"] - want)) > 0.03:
                bad.append((s["moonYaw"], round(want, 3)))
        check("L04 四次采样月面朝向都对准地球（潮汐锁定）", not bad, bad)
        moved = abs(wrap_pi(samples[-1]["angle"] - samples[0]["angle"]))
        check("L04 月球确实在公转（不是静止贴着地球）", moved > 0.05, moved)
        yaws = [s["moonYaw"] for s in samples]
        check("L04 潮汐锁定下月面朝向随轨道改变（自转与公转同步）",
              len(set(yaws)) == len(yaws), yaws)

        # ---- L05 月相与盈亏 ----
        # 月相每帧在推进，探针与界面必须来自同一次读取，否则会读到
        # 前后两帧、恰好在「上弦月 → 盈凸月」的分界线两边。
        snap = page.evaluate("""() => ({
          info: window.__galaxy.earthMoonInfo(),
          badge: {
            show: document.getElementById('moonBadge').classList.contains('show'),
            phase: document.getElementById('moonPhase').textContent,
            info: document.getElementById('moonInfo').textContent
          }
        })""")
        e = snap["info"]
        badge = snap["badge"]
        frac_true = (1 - math.cos(e["angle"])) / 2
        check("L05 亮比例 = (1 - cos 相位角) / 2",
              abs(e["frac"] - frac_true) < 0.01, (e["frac"], frac_true))
        # 探针的 frac 保留三位小数，恰好压在分界线上时可能和内部未舍入的值
        # 给出不同的名字；只在边界附近放宽容差，其余要求精确一致。
        cand = {phase_name(f, e["waxing"])
                for f in (e["frac"] - 0.0006, e["frac"], e["frac"] + 0.0006)}
        check("L05 月相名与亮比例、盈亏方向一致",
              e["phase"] in cand, (e["phase"], e["frac"], e["waxing"]))
        check("L05 月相卡出现在地月系层", badge["show"] is True, badge)
        check("L05 月相卡文字与探针一致",
              badge["phase"] == e["phase"], (badge["phase"], e["phase"]))
        check("L05 月相卡写出月地距离（万公里）",
              "月地距离" in badge["info"] and "万公里" in badge["info"],
              badge["info"])
        # 384,400 km × 当前距离比例，换算成「万公里」；界面按 0.1 万取整，
        # 所以只比数值，不要求字符串逐字相同。
        km_wan = 384400 * (e["sepWu"] / e["distanceWu"]) / 1000 / 10
        got = re.search(r"([0-9.]+) 万公里", badge["info"])
        check("L05 月地距离读数与轨道半径一致",
              abs(km_wan - 38.44) < 2.2 and got
              and abs(float(got.group(1)) - km_wan) <= 0.15,
              (round(km_wan, 2), badge["info"]))

        # ---- L06 聚焦月球：近裁剪不再切掉球面 ----
        left, d = wait_for(page, lambda s: s["state"] == "EARTHMOON_IDLE")
        sidebar_items = sidebar(page)
        check("L06 地月系侧栏只有地球与月球",
              [i["id"] for i in sidebar_items] == ["earth", "moon"],
              sidebar_items)
        page.evaluate("() => window.__galaxy.focusEarthMoonPart('moon')")
        focused, d = wait_for(page, lambda s: s["focusedEarthMoon"] == "moon")
        check("L06 聚焦月球", focused, d["focusedEarthMoon"])
        p = panel(page)
        check("L06 月球资料面板打开且是中文名",
              p["show"] is True and p["zh"] == "月球", p)
        check("L06 面板写明潮汐锁定与 27.322 天",
              "潮汐锁定" in p["stats"] and "27.322" in p["stats"], p["stats"][:120])
        check("L06 深链接同步到 #earthmoon/moon",
              page.evaluate("() => location.hash") == "#earthmoon/moon",
              page.evaluate("() => location.hash"))
        check("L06 侧栏高亮月球",
              any(i["id"] == "moon" and i["active"] for i in sidebar(page)),
              sidebar(page))

        e2 = info(page)
        # 相机最近可以推到 minR=0.6；月球半径 0.273，球面离相机最近 0.327，
        # 若沿用全局 near=0.5 就会切掉正面，这里量出余量。
        wait_for(page, lambda s: s["radius"] < 4, tries=40)
        e2 = info(page)
        margin = e2["camRadius"] - e2["moonR"]
        check("L06 相机已经推进到月球附近", e2["camRadius"] < 4, e2["camRadius"])
        check("L06 球面到相机的距离远大于 near（不会被裁切）",
              margin > e2["near"] * 4, (margin, e2["near"]))
        check("L06 近距下月球直径超过 40 像素",
              e2["moonPx"] > 40, e2["moonPx"])

        page.evaluate("() => window.__galaxy.focusEarthMoonPart('earth')")
        fe, d = wait_for(page, lambda s: s["focusedEarthMoon"] == "earth")
        check("L07 切回聚焦地球", fe, d["focusedEarthMoon"])
        p = panel(page)
        check("L07 地球面板说明这一层是真实比例",
              p["zh"] == "地球" and "真实比例" in p["desc"], p["zh"])
        check("L07 地球面板写明 60.3 世界单位",
              "60.3" in p["stats"], p["stats"][:120])

        # ---- L08 退出地月系：恢复太阳系的相机与近裁剪 ----
        page.evaluate("() => window.__galaxy.exitEarthMoon()")
        back, d = wait_for(page, lambda s: (
            s["state"] in ("SOLAR_IDLE", "PLANET_FOCUS")
            and s["layers"]["earthmoon"] < 0.02), tries=80)
        check("L08 退出地月系，地月层淡出", back, d)
        near_after = page.evaluate("() => window.__galaxy.earthMoonInfo()"
                                   " && window.__galaxy.earthMoonInfo().near")
        check("L08 退出后近裁剪恢复 0.5",
              near_after is None or abs(near_after - 0.5) < 1e-9, near_after)
        check("L08 退出后不再保留地球/月球聚焦",
              d["focusedEarthMoon"] is None, d["focusedEarthMoon"])
        # 从 #earthmoon 冷启动的链路是「太阳系 → 聚焦地球 → 地月系」，
        # 退出后理应回到地球聚焦态，所以 #solar 与 #body/earth 都算对。
        check("L08 深链接回到太阳系",
              page.evaluate("() => location.hash") in ("#solar", "#body/earth"),
              page.evaluate("() => location.hash"))

        # ---- L09 深链接直达地月系与月球 ----
        boot(page, "#earthmoon/moon")
        ok, d = wait_for(page, lambda s: (
            s["state"] == "EARTHMOON_IDLE" and s["focusedEarthMoon"] == "moon"))
        check("L09 #earthmoon/moon 直接进入并聚焦月球", ok, d)
        check("L09 深链接进入后面板是月球",
              panel(page)["zh"] == "月球", panel(page)["zh"])

        boot(page, "#earthmoon/notapart")
        page.wait_for_timeout(1500)
        check("L09 非法部件 id 被忽略，停在银河层",
              dump(page)["state"] == "GALAXY_IDLE", dump(page)["state"])

        # ---- L10 从太阳系聚焦地球后继续放大进入 ----
        boot(page, "#body/earth")
        ok, d = wait_for(page, lambda s: (
            s["state"] == "PLANET_FOCUS" and s["focusedEarthMoon"] is None),
            tries=80)
        check("L10 #body/earth 聚焦地球", ok, d["state"])
        entered = page.evaluate("() => window.__galaxy.enterEarthMoon()")
        check("L10 从地球聚焦态可进入地月系", entered is True, entered)
        arrived, d = wait_for(page, lambda s: (
            s["state"] == "EARTHMOON_IDLE" and s["layers"]["earthmoon"] > 0.98))
        check("L10 抵达地月系", arrived, d["state"])

        legend = page.evaluate("() => document.getElementById('legendBody').textContent")
        check("L10 地月系图例说明潮汐锁定、真实比例与时间倍率",
              "潮汐锁定" in legend and "真实比例" in legend and "109" in legend,
              legend[:120])

        browser.close()

    check("无页面错误", not errors, "; ".join(errors[:3]))
    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
