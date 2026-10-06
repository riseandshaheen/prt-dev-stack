"""Drive a match to a timeout win and check the ended state end to end (API and UI)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import stack as S
from vis.keccak import keccak256
from playwright.sync_api import sync_playwright

with S.stack() as ctx:
    eth, res = ctx["eth"], ctx["result"]
    eth.call("anvil_mine", [hex(330 - eth.block_number())])
    S.drive.win_by_timeout(eth, res["tournament"], 3, res["roots"][0], res["roots"][1],
                           keccak256(b"sybil b final state"), b"b")
    time.sleep(3)
    ep = S.get(f"/api/v1/apps/{S.drive.APP}/epochs/0")
    m = ep["dispute"]["matches"][0]
    assert m["deleted"]["reason"] == "timeout" and m["deleted"]["winner"] == "two", m["deleted"]
    loser = next(c for c in ep["dispute"]["commitments"] if c["root"] == res["roots"][0])
    assert loser["eliminated_at"] == m["deleted"]["block"], loser
    ov = S.get("/api/v1/overview")
    assert any("winner matches no node" in a["text"] for a in ov["alerts"]), ov["alerts"]
    assert S.get(f"/api/v1/apps/{S.drive.APP}")["epochs"][-1]["status"] == "decided"
    print("standing after timeout:", ep["dispute"]["standing"], "| app page:",
          [e["status"] for e in S.get(f"/api/v1/apps/{S.drive.APP}")["epochs"]])
    with sync_playwright() as p:
        b = p.chromium.launch(); page = b.new_page(viewport={"width": 1360, "height": 900})
        errs = []; page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"http://127.0.0.1:{S.VIS}/#/app/{S.drive.APP}/epoch/0/dispute"); page.wait_for_timeout(1500)
        page.screenshot(path="/tmp/shots/ended.png", full_page=True)
        print("page errors:", errs); b.close()
    print("ended-match checks passed")
