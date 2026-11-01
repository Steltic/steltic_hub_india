#!/usr/bin/env python3
"""live_page_check.py -- open the live India page in a headless browser and check it (handoff section 5, step 8).

    python site/tools/live_page_check.py [--url https://stelticai.com/india.html] [--expect 24,15,24] [--shot out.png]

Prints the page errors and failed requests (none expected), the number of entries in each example list (hot-rolled,
cold-formed, nonlinear: --expect gives the counts the publish should show) and the nonlinear table rows marked
"under review" or with a lower bound. Needs Playwright (pip install playwright; playwright install chromium).
Exit code 1 when a page error, a failed request or a count mismatch is found.
"""
import argparse
import asyncio
import sys


async def run(url, expect, shot):
    from playwright.async_api import async_playwright
    bad_count = False
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1300, "height": 900})
        errs, bad = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
        await pg.goto(url, wait_until="networkidle", timeout=90000)
        await pg.wait_for_timeout(2500)
        print("page errors:", errs[:5])
        print("failed requests:", bad[:8])
        counts = await pg.evaluate("[...document.querySelectorAll('select')].map(s=>s.options.length-1)")
        print("example lists (entries):", counts)
        if expect and counts[:len(expect)] != expect:
            print("COUNT MISMATCH: expected", expect)
            bad_count = True
        rows = await pg.evaluate("[...document.querySelectorAll('#nlTable tbody tr')].map(r=>r.innerText.replace(/\\s+/g,' '))")
        print("nonlinear rows:", len(rows), "with >=:", sum("≥" in r for r in rows),
              "under review:", sum("under review" in r for r in rows))
        if shot:
            await pg.screenshot(path=shot, full_page=False)
        await b.close()
    return 1 if (errs or bad or bad_count) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="https://stelticai.com/india.html")
    ap.add_argument("--expect", default="", help="entries of the hot-rolled, cold-formed and nonlinear lists, e.g. 24,15,24")
    ap.add_argument("--shot", default="", help="save a screenshot here")
    a = ap.parse_args()
    expect = [int(x) for x in a.expect.split(",") if x.strip()]
    return asyncio.run(run(a.url, expect, a.shot))


if __name__ == "__main__":
    sys.exit(main())
