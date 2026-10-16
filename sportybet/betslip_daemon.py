"""A warm, signed-in SportyBet betslip in one long-lived Scrapling browser (runs in the worker container).

    python betslip_daemon.py        # serves 127.0.0.1:8765 inside the container; betslip.py is the client

Why: the bot's stage-bet.js launches Chrome, copies the session in and loads two pages for every bet
(~20-30 s). Here the browser and the signed-in context stay open, so a stake only opens the booking
code's page and clicks through.

It is a port of claudesportybot/puppeteer/stage-bet.js and keeps every one of its rules:
- placement goes through the real betslip UI (POST /orders/order is encrypted by the page; it is never
  forged or replayed);
- a code with legs no longer offered is refused; the stake is typed only when the slip holds exactly
  the code's legs (old legs are cleared and the code reloaded);
- the stake box must read the stake; --min-odds / --max-odds are checked against the slip's total; the
  REAL/SIM switch must read REAL;
- one click on Place Bet, the confirm panel must name exactly the stake, one click on Confirm; the
  verdict is the page's own "Submission Successful" dialog; its Rebet button is never touched;
- anything that goes wrong after the Place Bet click is "unclear" (exit 3), never retried.
Scrapling's fetch retries are set to 1 and page_action errors are recorded here, not swallowed, so a
click can never run twice.

The login comes from the bot's saved session (claudesportybot/.chrome-profile/sportybuddy-session.json,
mounted read-only at /session); pulld's keep-alive renews that file hourly and it is reloaded into the
browser before a stake whenever it changes.
"""
import json, os, random, re, time, traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, urlopen

from scrapling.fetchers import DynamicSession

PORT = int(os.environ.get("BETSLIP_PORT", "8765"))
SESSION_FILE = os.environ.get("SESSION_FILE", "/session/sportybuddy-session.json")
BASE = "https://www.sportybet.com/gh"
API = "https://www.sportybet.com/api/gh"
PACE = float(os.environ.get("SB_PACE", "0.5"))

PLACE_BET = "[data-op=desktop-betslip-place-bet-button]"
CONFIRM = "[data-op=desktop-betslip-confirm-button]"
LEG = "#j_betslip .m-list .m-item"
STAKE_BOX = "#j_stake_0 input.m-input"


def pause(page, lo, hi):
    page.wait_for_timeout(random.uniform(lo, hi) * PACE)


GLANCE, READ, DECIDE = (600, 1500), (1500, 4000), (1200, 2800)


def load_session():
    s = json.load(open(SESSION_FILE))
    cookies = []
    for c in s.get("cookies", []):
        ck = {"name": c["name"], "value": c["value"], "domain": c.get("domain", ".sportybet.com"), "path": c.get("path", "/"),
              "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure"))}
        if c.get("expires") and c["expires"] > 0:
            ck["expires"] = c["expires"]
        if c.get("sameSite") in ("Strict", "Lax", "None"):
            ck["sameSite"] = c["sameSite"]
        cookies.append(ck)
    return cookies, s.get("ua"), os.path.getmtime(SESSION_FILE)


def read_code(code, ua):
    req = Request(f"{API}/orders/share/{code}", headers={"User-Agent": ua, "Accept": "application/json"})
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def wait_for(page, fn, timeout_ms=20_000, step_ms=250):
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        v = fn()
        if v:
            return v
        page.wait_for_timeout(step_ms)
    return None


def slip_legs(page):
    return page.eval_on_selector_all(LEG, """items => items.map(it => ({
        pick: it.querySelector('.m-item-play span')?.innerText.trim() ?? '',
        match: it.querySelector('.m-item-team')?.innerText.trim() ?? '',
        market: it.querySelector('.m-item-market')?.innerText.trim() ?? '',
        odds: it.querySelector('.m-item-odds .m-text-main')?.innerText.trim() ?? ''}))""")


def clear_slip(page):
    for _ in range(100):
        d = page.query_selector(f"{LEG} .m-icon-delete")
        if not d:
            return True
        pause(page, *GLANCE)
        d.click()
        page.wait_for_timeout(300)
    return page.query_selector(LEG) is None


SLIP_TOTALS = """() => {
  const txt = el => el?.innerText.replace(/\\s+/g, ' ').trim() ?? null;
  const root = document.querySelector('#j_betslip');
  const leaves = [...(root?.querySelectorAll('.m-stake *, .m-flexibet *') ?? [])]
    .filter(e => e.children.length === 0 && (e.innerText ?? '').trim());
  const at = leaves.findIndex(e => /^(Total )?Odds$/i.test(e.innerText.trim()));
  const next = at < 0 ? null : leaves.slice(at + 1).find(e => /^[\\d.,]+$/.test(e.innerText.trim()));
  return { totalOdds: next ? next.innerText.trim().replace(/,/g, '') : null,
           potentialWin: txt(document.querySelector('[data-op=potential-win-value]')),
           error: txt(root?.querySelector('.m-error-tips')) || null };
}"""


class Betslip:
    def __init__(self):
        self.cookies, self.ua, self.loaded_at = load_session()
        self.session = DynamicSession(headless=True, useragent=self.ua, cookies=self.cookies, retries=1,
                                      network_idle=False, load_dom=True, google_search=False, timeout=60_000,
                                      disable_resources=False, max_pages=1)
        self.session.__enter__()
        self.started = time.time()

    def close(self):
        try:
            self.session.__exit__(None, None, None)
        except Exception:
            pass

    def refresh_cookies(self, page):
        """pulld's keep-alive rewrites the session file hourly: load the new cookies before staking."""
        if os.path.getmtime(SESSION_FILE) != self.loaded_at:
            self.cookies, self.ua, self.loaded_at = load_session()
            page.context.add_cookies(self.cookies)

    def stake(self, code, stake, place=False, to_confirm=False, min_odds=None, max_odds=None):
        """The stage-bet.js flow. Returns {exit, lines}: 0 placed (or staged / checked OK), 1 nothing placed,
        3 unclear (after the Place Bet click)."""
        out = {"exit": 1, "lines": [], "clicked_place": False}
        say = out["lines"].append

        res = read_code(code, self.ua)
        if res.get("bizCode") != 10000:
            say(f"Code {code} did not load: bizCode {res.get('bizCode')} {res.get('message') or ''}. Nothing was placed.")
            return out
        wanted = len((res["data"].get("ticket") or {}).get("selections") or [])
        gone = len(res["data"].get("unavailableOutcomes") or [])
        if gone:
            say(f"Code {code}: {gone} of {wanted} legs are no longer offered. Nothing was placed.")
            return out

        def action(page):
            try:
                self._flow(page, code, stake, wanted, place, to_confirm, min_odds, max_odds, out, say)
            except Exception as e:  # noqa: BLE001
                say(f"error: {e!r}")
                say(traceback.format_exc(limit=2))
                if out["clicked_place"]:
                    out["exit"] = 3
                    say("Result unclear after the Place Bet click. Check Bet History before anything else.")
                else:
                    out["exit"] = 1
                    say("Nothing was placed.")

        self.session.fetch(f"{BASE}/?shareCode={code}", page_setup=self.refresh_cookies, page_action=action)
        return out

    def _flow(self, page, code, stake, wanted, place, to_confirm, min_odds, max_odds, out, say):
        if not any(c["name"] == "accessToken" for c in page.context.cookies("https://www.sportybet.com")):
            say("Not signed in (no accessToken): run `npm run login` in claudesportybot. Nothing was placed.")
            return
        pause(page, *READ)
        tab = page.query_selector('#j_betslip [data-name="Betslip"]')
        if tab:
            tab.click()
        n = wait_for(page, lambda: len(page.query_selector_all(LEG)) or None, 15_000)
        if (n or 0) != wanted:
            # Old legs share the slip, or the code has not fully loaded: empty it and load the code again.
            say(f"slip shows {n or 0} legs, the code has {wanted}: clearing and reloading the code")
            if not clear_slip(page):
                say("Could not empty the betslip. Nothing was placed.")
                return
            page.goto(f"{BASE}/?shareCode={code}")
            pause(page, *READ)
            tab = page.query_selector('#j_betslip [data-name="Betslip"]')
            if tab:
                tab.click()
            n = wait_for(page, lambda: (len(page.query_selector_all(LEG)) == wanted) or None, 20_000)
            if not n:
                say(f"The slip shows {len(page.query_selector_all(LEG))} legs, the code has {wanted}. Nothing was placed.")
                return
        legs = slip_legs(page)

        box = page.wait_for_selector(STAKE_BOX, timeout=10_000)
        pause(page, *GLANCE)
        box.click()
        page.keyboard.press("End")
        for _ in range(len(box.input_value()) + 2):
            page.keyboard.press("Backspace")
            page.wait_for_timeout(random.uniform(60, 160) * PACE)
        page.wait_for_timeout(random.uniform(300, 800) * PACE)
        for ch in f"{stake:g}":
            page.keyboard.type(ch)
            page.wait_for_timeout(random.uniform(90, 240) * PACE)
        page.keyboard.press("Tab")
        page.wait_for_timeout(800)

        typed = page.eval_on_selector(STAKE_BOX, "el => el.value")
        totals = page.evaluate(SLIP_TOTALS)
        if not totals["totalOdds"] and len(legs) == 1:
            totals["totalOdds"] = legs[0]["odds"]
        say(f"{code} — {len(legs)} legs on the slip")
        for l in legs:
            say(f"  {l['odds']:>6}  {l['match']}\n          {l['market']} -> {l['pick']}")
        say(f"  stake          GHS {typed}")
        say(f"  total odds     {totals['totalOdds']}")
        if totals["potentialWin"]:
            say(f"  potential win  GHS {totals['potentialWin']}")
        if totals["error"]:
            say(f"  slip says      {totals['error']}")

        ok = True
        try:
            ok = float(typed) == float(stake)
        except ValueError:
            ok = False
        if not ok:
            say(f'The stake box reads "{typed}", not {stake}.')
        shown = float(totals["totalOdds"]) if totals["totalOdds"] else None
        if min_odds is not None and (shown is None or shown < min_odds):
            say(f"Total odds {shown} are below --min-odds {min_odds}: prices moved.")
            ok = False
        if max_odds is not None and (shown is None or shown > max_odds):
            say(f"Total odds {shown} are above --max-odds {max_odds}: prices rose.")
            ok = False
        mode = None
        knob = page.query_selector("#j_betslip .switch-box .inside-btn")
        if knob:
            mode = knob.inner_text().strip()
        say(f"  mode           {mode or '(switch not found)'}")
        if (place or to_confirm) and mode != "REAL":
            say(f"The betslip is in {mode or 'an unknown mode'}, not REAL.")
            ok = False
        btn = page.query_selector(f"{PLACE_BET}:not([disabled])")
        if not btn or not btn.is_visible():
            say("The Place Bet button is not on the slip.")
            ok = False

        if not (place or to_confirm):
            say("Staged (dry run). Nothing was placed." if ok else "Staged with problems above. Nothing was placed.")
            out["exit"] = 0 if ok else 1
            return
        if not ok:
            say("--place: not placing — fix the problems above first. Nothing was placed.")
            return

        order = {"status": None}
        page.on("response", lambda r: order.update(status=r.status)
                if r.request.method == "POST" and re.search(r"/orders/order(\?|$)", r.url) else None)
        pause(page, *DECIDE)
        out["clicked_place"] = True
        page.click(PLACE_BET)
        panel = wait_for(page, lambda: (page.query_selector("#j_betslip .m-comfirm-wrapper") or None) and
                         re.sub(r"\s+", " ", page.inner_text("#j_betslip .m-comfirm-wrapper")).strip(), 10_000)
        pay = re.compile(rf"(About to pay|Confirm to Pay) GHS {re.escape(f'{stake:.2f}')}\b", re.I)
        if not panel or not pay.search(panel):
            say(f'The confirm panel reads "{panel or "(none)"}", not GHS {stake:.2f}. Not confirming. Nothing was placed.')
            out["exit"] = 1
            return
        if to_confirm:
            say(f'Confirm panel OK: "{panel}". Test mode: closing without confirming. Nothing was placed.')
            out["exit"] = 0
            return

        pause(page, *DECIDE)
        page.click(CONFIRM)

        def verdict():
            t = page.inner_text("body")
            if re.search(r"Submission Successful", t, re.I):
                return "ok"
            tip = page.evaluate("""() => [...document.querySelectorAll('.m-error-tips, [class*=toast], [class*=dialog]')]
                .map(e => e.offsetParent && e.innerText.trim()).find(Boolean) || null""")
            return f"fail:{tip[:200]}" if tip else None
        v = wait_for(page, verdict, 15_000)
        if v == "ok":
            say(f"PLACED  GHS {stake:.2f} on {code} at {totals['totalOdds']}"
                + (f" — potential win GHS {totals['potentialWin']}" if totals["potentialWin"] else ""))
            say("The result dialog is left open (its Rebet button is never touched).")
            out["exit"] = 0
        elif v and v.startswith("fail:"):
            say(f"NOT PLACED, per the page: {v[5:]}\nConfirm in Bet History before trying again.")
            out["exit"] = 1
        else:
            say(f"Result unclear — order call {'HTTP ' + str(order['status']) if order['status'] else 'never seen'}, "
                "no result dialog.\nCheck Bet History before doing anything else.")
            out["exit"] = 3


BETSLIP = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "up_s": round(time.time() - BETSLIP.started), "session_mtime": BETSLIP.loaded_at})
        else:
            self._send(404, {"error": "unknown"})

    def do_POST(self):
        if self.path != "/stake":
            return self._send(404, {"error": "unknown"})
        req = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        t = time.time()
        try:
            res = BETSLIP.stake(req["code"], float(req["stake"]), place=bool(req.get("place")),
                                to_confirm=bool(req.get("to_confirm")), min_odds=req.get("min_odds"), max_odds=req.get("max_odds"))
        except Exception as e:  # noqa: BLE001
            # Any failure here is before the page flow ran, or inside it with its own verdict recorded.
            res = {"exit": 1, "lines": [f"betslip error before placing: {e!r}. Nothing was placed."]}
        res["seconds"] = round(time.time() - t, 1)
        self._send(200, res)


def main():
    global BETSLIP
    BETSLIP = Betslip()
    print(f"betslip daemon on 127.0.0.1:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()   # one request at a time: one stake at a time


if __name__ == "__main__":
    main()
