"""Boot the test stack and screenshot every view (desktop and phone), checking for XSS and console errors."""
import sys, time, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import stack as S
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"); OUT.mkdir(exist_ok=True)
APP = S.drive.APP
with S.stack() as ctx, sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1360, "height": 900})
    errors, dialogs = [], []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    base = f"http://127.0.0.1:{S.VIS}/"
    views = [("overview", "#/"), ("app", f"#/app/{APP}"), ("dispute", f"#/app/{APP}/epoch/0/dispute"),
             ("agreement", f"#/app/{APP}/epoch/0/agreement"), ("inputs", f"#/app/{APP}/epoch/1/inputs"),
             ("epoch0tx", f"#/app/{APP}/epoch/0/transactions"), ("ledger", f"#/app/{APP}/transactions")]
    for name, frag in views:
        page.goto(base + frag); page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    phone = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    phone.on("pageerror", lambda e: errors.append(str(e)))
    for name, frag in (("m-overview", "#/"), ("m-dispute", f"#/app/{APP}/epoch/0/dispute")):
        phone.goto(base + frag); phone.wait_for_timeout(1200)
        phone.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    # Stale banner: stop the server's stream by killing nothing; instead check the node-down path renders.
    ctx["ref"].fail = True; time.sleep(2.5)
    page.goto(base + "#/"); page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / "node-down.png"), full_page=False)
    ctx["ref"].fail = False
    print("console errors:", errors)
    print("dialogs (XSS would show here):", dialogs)
    b.close()
