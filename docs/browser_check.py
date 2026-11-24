import os, re, sys, time
from playwright.sync_api import sync_playwright
BASE="http://localhost:5173"
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1400,"height":900})
    msgs=[]; pg.on("console", lambda m: msgs.append(m.text) if m.type=="error" else None)
    pg.goto(BASE); pg.fill("input[type=password]","op-secret"); pg.click("button:has-text('Sign in')")
    pg.wait_for_selector("text=Traffic routing", timeout=15000)
    print("logged in as:", pg.inner_text(".user").split("\n")[0])
    body=pg.inner_text("body")
    print("'no API' label present:", ("no api" in body.lower()) or ("rule-based rca" in body.lower()))
    pg.screenshot(path="/tmp/shot1_before.png")
    t0=time.time()
    print("waiting for popup WITHOUT clicking anything...")
    pg.wait_for_selector("[role=dialog]", timeout=90000)
    print(f"popup appeared by itself after {time.time()-t0:.0f}s")
    time.sleep(2)
    txt=pg.inner_text("[role=dialog]")
    print("--- popup text ---\n"+txt[:700])
    pg.screenshot(path="/tmp/shot2_popup.png")
    assert "server-1" in txt and "server-2" in txt, "popup does not show both servers"
    pg.click("[role=dialog] button:has-text('Approve') >> nth=0")
    time.sleep(3)
    pg.screenshot(path="/tmp/shot3_after_approve.png")
    txt2=pg.inner_text("[role=dialog]") if pg.query_selector("[role=dialog]") else ""
    print("--- popup after approve ---\n"+txt2[:500])
    # close any remaining popup (e.g. a second proposal) and read routing panel
    if pg.query_selector("[role=dialog]"): pg.keyboard.press("Escape"); time.sleep(1)
    panel=pg.inner_text("body")
    i=panel.find("Traffic routing"); print("--- routing panel ---\n"+panel[i:i+420])
    print("console errors:", msgs[:3])
    b.close()