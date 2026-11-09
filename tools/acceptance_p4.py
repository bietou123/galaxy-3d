"""P4 验收：本星系群与其他星系的介绍（docs/prompt-100k 第四卷、第十卷 J 组）。

总纲 0.5 第三条要求：向外拉远进入本星系群，看到 M31 比银河系更大、
LMC/SMC 紧邻银河系、点开有中文介绍。这份脚本就验证这三件事，
外加深链接与返回栈。

无头环境（SwiftShader）里 requestAnimationFrame 很慢，补间按每帧 dt 推进，
所以等待一律轮询探针状态，不写死 sleep 时长。

用法：
    pip install playwright
    python -m playwright install chromium
    python tools/acceptance_p4.py
"""

import math
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


def boot(page, hash_suffix=""):
    """冷启动：先离开当前文档，保证带 hash 的地址真的重新加载一次。
    只改片段会走 same-document 导航，测不出「打开分享链接」的真实路径。"""
    page.goto("about:blank")
    page.goto(PAGE.as_uri() + hash_suffix)
    page.wait_for_selector("#boot.gone", state="attached", timeout=60000)
    page.wait_for_timeout(600)


def dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


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

        boot(page)
        check("探针 window.__galaxy 可用",
              page.evaluate("() => typeof window.__galaxy.dump") == "function")

        # ---- J01 成员数据：中文名、英文名、介绍、史话、类型、距离齐备 ----
        gals = page.evaluate("() => window.__galaxy.galaxies()")
        check("J01 本星系群至少有 8 个有名成员", len(gals) >= 8, len(gals))
        missing = [g["id"] for g in gals
                   if not g["zh"] or not g["en"] or not g["type"] or not g["distText"]]
        check("J01 中文名/英文名/类型/距离都不为空", not missing, missing)
        thin = [g["id"] for g in gals if g["introLen"] < 150]
        check("J01 每个成员都有中文介绍（≥150 字）", not thin,
              [(g["id"], g["introLen"]) for g in gals if g["introLen"] < 150])
        nohist = [g["id"] for g in gals if g["noteLen"] < 60]
        check("J01 每个成员都有史话/观测记录（≥60 字）", not nohist,
              [(g["id"], g["noteLen"]) for g in gals if g["noteLen"] < 60])
        by_id = {g["id"]: g for g in gals}
        check("J01 真实比例：仙女座星系直径大于银河系",
              by_id["m31"]["radius"] > by_id["milkyway"]["radius"],
              (by_id["milkyway"]["radius"], by_id["m31"]["radius"]))
        check("J01 真实位置：大麦哲伦云比小麦哲伦云更近银河系",
              math.dist(by_id["lmc"]["pos"], by_id["milkyway"]["pos"])
              < math.dist(by_id["smc"]["pos"], by_id["milkyway"]["pos"]))

        # ---- J02 向外拉远进入本星系群 ----
        before = dump(page)
        check("J02 起始仍在银河层", before["state"] == "GALAXY_IDLE", before["state"])
        entered = page.evaluate("() => window.__galaxy.enterGroup()")
        check("J02 enterGroup 返回 true", entered is True, entered)
        arrived, d = wait_for(page, lambda s: (
            s["state"] == "LOCALGROUP_IDLE" and s["layers"]["localgroup"] > 0.98))
        check("J02 抵达本星系群层", arrived, d)
        check("J02 银河层已淡出", d["layers"]["galaxy"] < 0.02, d["layers"])
        check("J02 相机视场切到 50°",
              page.evaluate("() => Math.round(window.__galaxy.fov() * 180 / Math.PI)") == 50)
        n_list = page.evaluate("() => document.querySelectorAll('#bodyList li').length")
        check("J02 侧栏列出全部有名字的成员（不是只列前几个）",
              n_list >= len(gals), (n_list, len(gals)))

        # ---- J03 M31 在屏幕上比银河系大 ----
        mw = page.evaluate("() => window.__galaxy.galaxyScreen('milkyway')")
        m31 = page.evaluate("() => window.__galaxy.galaxyScreen('m31')")
        m33 = page.evaluate("() => window.__galaxy.galaxyScreen('m33')")
        check("J03 四个主要成员都在画面内",
              all(p and 0 < p["x"] < 1280 and 0 < p["y"] < 800 for p in (mw, m31, m33)),
              (mw, m31, m33))
        check("J03 仙女座星系的屏幕尺寸大于银河系",
              m31["physPx"] > mw["physPx"], (mw["physPx"], m31["physPx"]))
        check("J03 银河系与三角座星系都大于最小可见尺寸",
              min(mw["sr"], m33["sr"]) >= 8, (mw["sr"], m33["sr"]))

        # ---- J04 LMC / SMC 紧邻银河系，且仍可分辨 ----
        lmc = page.evaluate("() => window.__galaxy.galaxyScreen('lmc')")
        smc = page.evaluate("() => window.__galaxy.galaxyScreen('smc')")
        check("J04 大麦哲伦云在银河系附近（屏幕上 < 80px）",
              dist(lmc, mw) < 80, dist(lmc, mw))
        check("J04 小麦哲伦云在银河系附近（屏幕上 < 90px）",
              dist(smc, mw) < 90, dist(smc, mw))
        check("J04 两个麦哲伦云都有可见的最小柔光",
              lmc["sr"] >= 8 and smc["sr"] >= 6, (lmc["sr"], smc["sr"]))
        check("J04 真实比例下它们本身只有几个像素（说明柔光是显示放大）",
              lmc["physPx"] <= 8, lmc["physPx"])

        # 推进到大麦哲伦云后，必须能分清这是两个天体
        page.evaluate("() => window.__galaxy.focusGalaxy('lmc')")
        # 无头环境里补间按帧推进，等相机真正落到位再量屏幕距离
        wait_for(page, lambda s: s["radius"] < 400, tries=40)
        lmc2 = page.evaluate("() => window.__galaxy.galaxyScreen('lmc')")
        smc2 = page.evaluate("() => window.__galaxy.galaxyScreen('smc')")
        sep = dist(lmc2, smc2)
        check("J04 推进到 LMC 后两个云分开，SMC 落在 LMC 亮核之外",
              sep > 40 and sep > 0.46 * lmc2["sr"], (sep, lmc2["sr"]))
        check("J04 推进后 SMC 仍然可见",
              smc2 and 0 < smc2["x"] < 1280 and smc2["sr"] >= 20, smc2)

        # ---- J05 点开成员有中文介绍 ----
        page.evaluate("() => window.__galaxy.focusGalaxy('m31')")
        page.wait_for_timeout(1500)
        d = dump(page)
        check("J05 聚焦后记录当前星系", d["focusedGalaxy"] == "m31", d["focusedGalaxy"])
        panel = page.evaluate("""() => {
          var info = document.getElementById('info');
          return {
            show: info.classList.contains('show'),
            zh: document.getElementById('infoZh').textContent,
            desc: document.getElementById('desc').textContent,
            stats: document.getElementById('stats').textContent,
            hist: document.getElementById('histText').textContent
          };
        }""")
        check("J05 信息面板已打开", panel["show"] is True, panel["show"])
        check("J05 面板标题是中文名", panel["zh"] == "仙女座星系", panel["zh"])
        check("J05 介绍正文足够长（≥150 字）", len(panel["desc"]) >= 150,
              len(panel["desc"]))
        check("J05 面板含类型/距离/质量等数据行",
              all(k in panel["stats"] for k in ("类型", "距离", "质量")),
              panel["stats"][:80])
        check("J05 面板注明柔光是显示放大", "显示放大" in panel["stats"])
        check("J05 面板有史话/观测记录", len(panel["hist"]) >= 60, len(panel["hist"]))

        # ---- J06 Esc 先退回全局构图，再退一层回银河系 ----
        page.keyboard.press("Escape")
        back, d = wait_for(page, lambda s: s["focusedGalaxy"] is None)
        check("J06 第一次 Esc 取消聚焦", back, d["focusedGalaxy"])
        check("J06 仍在银河群层（没有一次退两级）",
              d["state"] == "LOCALGROUP_IDLE", d["state"])
        check("J06 面板已关闭",
              page.evaluate("() => document.getElementById('info').classList.contains('show')")
              is False)

        page.keyboard.press("Escape")
        home, d = wait_for(page, lambda s: (
            s["state"] == "GALAXY_IDLE" and s["layers"]["galaxy"] > 0.9
            and abs(s["radius"] - before["radius"]) < max(60, before["radius"] * 0.08)))
        check("J06 第二次 Esc 回到银河层", home, d["state"])
        check("J06 相机半径回到进入前附近",
              abs(d["radius"] - before["radius"]) < max(60, before["radius"] * 0.08),
              (before["radius"], d["radius"]))

        # ---- J06a 聚焦后点空白处也能退出聚焦 ----
        # M31 的柔光在近距离有约 300px 半径，拾取热区若不封顶，点半个
        # 屏幕都算点中它，用户想取消聚焦反而会又点回同一个星系。
        page.evaluate("() => window.__galaxy.enterGroup()")
        wait_for(page, lambda s: s["state"] == "LOCALGROUP_IDLE")
        page.evaluate("() => window.__galaxy.focusGalaxy('m31')")
        wait_for(page, lambda s: s["focusedGalaxy"] == "m31" and s["radius"] < 900)
        page.mouse.click(640, 230)
        blank, d = wait_for(page, lambda s: s["focusedGalaxy"] is None)
        check("J06a 聚焦后点空白处取消聚焦", blank, d["focusedGalaxy"])
        check("J06a 取消聚焦后仍在本星系群层",
              d["state"] == "LOCALGROUP_IDLE", d["state"])

        # ---- J07 深链接 ----
        boot(page, "#localgroup")
        ok, d = wait_for(page, lambda s: s["state"] == "LOCALGROUP_IDLE")
        check("J07 #localgroup 直接进入本星系群", ok, d["state"])

        boot(page, "#localgroup/m33")
        ok, d = wait_for(page, lambda s: (
            s["state"] == "LOCALGROUP_IDLE" and s["focusedGalaxy"] == "m33"))
        check("J07 #localgroup/m33 直接聚焦三角座星系", ok, d)
        check("J07 深链接进入后面板显示三角座星系",
              page.evaluate("() => document.getElementById('infoZh').textContent") == "三角座星系")

        boot(page, "#localgroup/notagalaxy")
        page.wait_for_timeout(1500)
        d = dump(page)
        check("J07 非法成员 id 被静默忽略，停在银河层",
              d["state"] == "GALAXY_IDLE", d["state"])

        # ---- J08 图例按层换内容 ----
        boot(page, "#localgroup")
        wait_for(page, lambda s: s["state"] == "LOCALGROUP_IDLE")
        legend = page.evaluate("() => document.getElementById('legendBody').textContent")
        check("J08 本星系群层图例讲成员与显示放大",
              "柔光" in legend and "本星系群" in legend, legend[:60])

        browser.close()

    check("无页面错误", not errors, "; ".join(errors[:3]))
    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
