"""P5 验收：其他恒星系统（docs/prompt-100k 第五卷 5.1–5.4、第十卷 K 组）。

用户要求「别的星系呢」「别的星系的名字还有介绍呢」「可以点击某个行星」。
本脚本验证：11 颗邻近恒星的数据齐备、能进入各自的系统、系统里有成员星与
已知系内行星的轨道、能点开有中文介绍的资料面板、深链接直达、Esc 逐级返回。

无头环境（SwiftShader）里 requestAnimationFrame 很慢，补间按每帧 dt 推进，
所以等待一律轮询探针状态，不写死 sleep 时长。

用法：
    pip install playwright
    python -m playwright install chromium
    python tools/acceptance_p5.py
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


def wait_for(page, predicate, tries=100, step_ms=500):
    d = dump(page)
    for _ in range(tries):
        if predicate(d):
            return True, d
        page.wait_for_timeout(step_ms)
        d = dump(page)
    return predicate(d), d


def boot(page, hash_suffix=""):
    """先离开文档再带 hash 打开，走真实的冷启动路径（片段导航测不出）。"""
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
        en: document.getElementById('infoEn').textContent,
        desc: document.getElementById('desc').textContent,
        stats: document.getElementById('stats').textContent,
        hist: document.getElementById('histText').textContent
      };
    }""")


def sidebar(page):
    return page.evaluate("""() => Array.from(
      document.querySelectorAll('#bodyList li')).map(function (li) {
        return { id: li.dataset.id, text: li.textContent };
      })""")


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

        # ---- K01 恒星数据表：中文名/英文名/光谱型/距离/质量/半径/温度 ----
        stars = page.evaluate("() => window.__galaxy.stars()")
        check("K01 邻近恒星共 11 颗", len(stars) == 11, len(stars))
        by_id = {s["id"]: s for s in stars}
        bad = [s["id"] for s in stars
               if not s["zh"] or not s["en"] or not s["spectral"]
               or s["distLy"] is None or not s["mass"]
               or not s["radiusRsun"] or not s["tempK"]]
        check("K01 名称/光谱型/距离/质量/半径/温度都不为空", not bad, bad)
        thin = [s["id"] for s in stars if s["introLen"] < 150]
        check("K01 每颗恒星都有中文介绍（≥150 字）", not thin,
              [(s["id"], s["introLen"]) for s in stars if s["introLen"] < 150])
        nohist = [s["id"] for s in stars if s["noteLen"] < 60]
        check("K01 每颗恒星都有史话/观测记录（≥60 字）", not nohist,
              [(s["id"], s["noteLen"]) for s in stars if s["noteLen"] < 60])
        check("K01 太阳走太阳系层，不在这层重复",
              by_id["sol"]["nav"] is None, by_id["sol"]["nav"])
        no_nav = [s["id"] for s in stars
                  if s["id"] != "sol" and not s["nav"]]
        check("K01 其余 10 颗恒星都能进入系统（有视距）", not no_nav, no_nav)
        check("K01 真实距离：比邻星所在系统是最近的恒星（太阳除外）",
              by_id["alphacen"]["distLy"] == min(
                  s["distLy"] for s in stars if s["id"] != "sol"),
              by_id["alphacen"]["distLy"])
        check("K01 参宿四是表内最大最远的恒星",
              max(stars, key=lambda s: s["radiusRsun"])["id"] == "betelgeuse"
              and max(stars, key=lambda s: s["distLy"])["id"] == "betelgeuse")
        check("K01 多星系统带成员星数据",
              len(by_id["alphacen"]["comps"]) >= 2
              and len(by_id["sirius"]["comps"]) == 2,
              (len(by_id["alphacen"]["comps"]), len(by_id["sirius"]["comps"])))
        check("K01 系内行星带状态（确认 / 候选）",
              all(w["status"] in ("confirmed", "candidate")
                  for s in stars for w in s["worlds"]))
        check("K01 比邻星系统含确认行星 b、d 与候选 c",
              {w["id"]: w["status"] for w in by_id["alphacen"]["worlds"]}
              == {"proxima-b": "confirmed", "proxima-d": "confirmed",
                  "proxima-c": "candidate"},
              by_id["alphacen"]["worlds"])

        # ---- K02 进入天苑四系统：从银河层的标记与侧栏都能进 ----
        legend = page.evaluate("() => document.getElementById('legendBody').textContent")
        check("K02 银河层图例提到邻近恒星与候选轨道",
              "邻近恒星" in legend and "候选" in legend, legend[:60])
        names = [item["text"] for item in sidebar(page)]
        check("K02 银河层侧栏列出 10 个邻近恒星入口",
              sum(1 for s in stars if s["id"] != "sol"
                  and any(s["zh"] in t for t in names)) == 10,
              len(names))

        boot(page, "#star/epseri")
        ok, d = wait_for(page, lambda s: (
            s["state"] == "STAR_IDLE" and s["layers"]["star"] > 0.98))
        check("K02 #star/epseri 直接进入恒星系统", ok, d)
        check("K02 银河层已淡出", d["layers"]["galaxy"] < 0.02, d["layers"])
        check("K02 记录当前恒星系统", d["focusedStar"] == "epseri", d["focusedStar"])
        check("K02 恒星层保持 55° 视场",
              page.evaluate("() => Math.round(window.__galaxy.fov() * 180 / Math.PI)") == 55)

        items = sidebar(page)
        check("K02 侧栏列出成员星与系内行星（天苑四 + 天苑四 b）",
              len(items) == 2 and items[0]["id"] == "a"
              and items[1]["id"] == "epseri-b", items)
        check("K02 侧栏标明行星状态",
              "确认行星" in items[1]["text"], items[1]["text"])

        # ---- K03 系内行星在轨道上、能点开、有中文资料 ----
        pos = page.evaluate("() => window.__galaxy.systemBodyScreen('epseri-b')")
        check("K03 天苑四 b 投影到画面内且轨道半径合理",
              pos and 0 < pos["x"] < 1280 and 0 < pos["y"] < 800
              and pos["px"] >= 1 and pos["orbitR"] > 0, pos)
        check("K03 天苑四 b 距恒星 3.52 AU 的压缩轨道",
              pos and 60 <= pos["orbitR"] <= 80, pos)

        page.evaluate("() => Array.from(document.querySelectorAll('#bodyList li'))"
                      ".find(li => li.dataset.id === 'epseri-b').click()")
        arrived, d = wait_for(page, lambda s: s["focusedSystem"] == "epseri-b")
        check("K03 点侧栏可聚焦系内行星", arrived, d["focusedSystem"])
        p = panel(page)
        check("K03 行星资料面板已打开", p["show"] is True, p["show"])
        check("K03 面板标题是中文名", p["zh"] == "天苑四 b", p["zh"])
        check("K03 面板含宿主/半长轴/公转周期/半径/状态",
              all(k in p["stats"] for k in ("宿主恒星", "半长轴", "公转周期",
                                            "半径", "状态")),
              p["stats"][:100])
        check("K03 面板有中文介绍", len(p["desc"]) >= 80, len(p["desc"]))
        check("K03 面板说明确认/候选的性质", "确认行星" in p["hist"], p["hist"][:60])
        check("K03 深链接同步到 #star/epseri/epseri-b",
              page.evaluate("() => location.hash") == "#star/epseri/epseri-b",
              page.evaluate("() => location.hash"))

        # 点击行星本体也应聚焦（用户要求「可以点击某个行星」）
        page.keyboard.press("Escape")
        wait_for(page, lambda s: s["focusedSystem"] is None)
        pos = page.evaluate("() => window.__galaxy.systemBodyScreen('epseri-b')")
        check("K03 未聚焦时行星仍在画面内可点击",
              pos and 0 < pos["x"] < 1280 and 0 < pos["y"] < 800, pos)
        if pos:
            page.mouse.click(pos["x"], pos["y"])
            clicked, d = wait_for(page, lambda s: s["focusedSystem"] == "epseri-b",
                                  tries=40)
            check("K03 直接点击行星本体可聚焦", clicked, d["focusedSystem"])

        # ---- K04 Esc 逐级返回：先回系统视角，再回银河层 ----
        page.keyboard.press("Escape")
        back, d = wait_for(page, lambda s: s["focusedSystem"] is None)
        check("K04 第一次 Esc 退出行星聚焦", back, d["focusedSystem"])
        check("K04 仍在恒星系统层", d["state"] == "STAR_IDLE", d["state"])
        check("K04 面板已关闭",
              page.evaluate("() => document.getElementById('info')"
                            ".classList.contains('show')") is False)

        page.keyboard.press("Escape")
        home, d = wait_for(page, lambda s: (
            s["state"] == "GALAXY_IDLE" and s["layers"]["galaxy"] > 0.9))
        check("K04 第二次 Esc 回到银河层", home, d["state"])
        check("K04 恒星层已淡出",
              d["layers"]["star"] < 0.02 and d["layers"]["galaxy"] > 0.9, d["layers"])

        # ---- K05 双星系统：成员星可看资料，没有系内行星 ----
        boot(page, "#star/sirius")
        ok, d = wait_for(page, lambda s: s["state"] == "STAR_IDLE")
        check("K05 进入天狼星双星系统", ok, d["state"])
        items = sidebar(page)
        check("K05 侧栏列出天狼星 A / B 两颗成员星",
              len(items) == 2 and items[0]["id"] == "a"
              and items[1]["id"] == "b", items)
        check("K05 成员星侧栏显示光谱型",
              "A1V" in items[0]["text"] and "DA2" in items[1]["text"], items)
        page.evaluate("() => Array.from(document.querySelectorAll('#bodyList li'))"
                      ".find(li => li.dataset.id === 'b').click()")
        page.wait_for_timeout(400)
        p = panel(page)
        check("K05 白矮星伴星有资料面板", p["show"] is True and p["zh"] == "天狼星 B",
              p)
        check("K05 面板写明白矮星与压缩间距",
              "DA2" in p["stats"] and "压缩" in p["stats"], p["stats"][:120])

        # ---- K06 比邻星系统：深链接直达行星、候选行星有标注 ----
        boot(page, "#star/alphacen/proxima-b")
        ok, d = wait_for(page, lambda s: (
            s["state"] == "STAR_IDLE" and s["focusedSystem"] == "proxima-b"))
        check("K06 #star/alphacen/proxima-b 进入并聚焦比邻星 b", ok, d)
        check("K06 比邻星 b 是确认行星", panel(page)["zh"] == "比邻星 b",
              panel(page)["zh"])
        items = sidebar(page)
        ids = [i["id"] for i in items]
        check("K06 侧栏含三颗成员星与三颗系内行星",
              all(x in ids for x in ("a", "b", "c",
                                     "proxima-b", "proxima-c", "proxima-d")), ids)
        cand = [i["text"] for i in items if i["id"] == "proxima-c"]
        check("K06 候选行星在侧栏标注为候选",
              cand and "候选行星" in cand[0], cand)

        boot(page, "#star/sol")
        page.wait_for_timeout(1200)
        d = dump(page)
        check("K06 #star/sol 被拒绝（太阳走 #solar）",
              d["state"] == "GALAXY_IDLE", d["state"])
        boot(page, "#star/notastar")
        page.wait_for_timeout(1200)
        check("K06 非法恒星 id 静默忽略",
              dump(page)["state"] == "GALAXY_IDLE", dump(page)["state"])
        boot(page, "#star/epseri/notabody")
        page.wait_for_timeout(1200)
        check("K06 非法系内行星 id 静默忽略",
              dump(page)["state"] == "GALAXY_IDLE", dump(page)["state"])

        # ---- K07 恒星层图例讲清压缩与候选 ----
        boot(page, "#star/vega")
        ok, d = wait_for(page, lambda s: s["state"] == "STAR_IDLE")
        check("K07 进入无行星的织女星系统也不报错", ok and d["focusedStar"] == "vega", d)
        items = sidebar(page)
        check("K07 无系内行星时侧栏只有恒星本体",
              len(items) == 1 and items[0]["id"] == "a", items)
        legend = page.evaluate("() => document.getElementById('legendBody').textContent")
        check("K07 恒星层图例说明间距压缩与候选/确认轨道",
              "压缩" in legend and "候选" in legend and "确认" in legend,
              legend[:80])

        browser.close()

    check("无页面错误", not errors, "; ".join(errors[:3]))
    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
