#!/usr/bin/env python3
"""Watch live football on SportyBet and stake the games that confirm the morning prediction.

Every day (UTC) it first pulls the day's games, their stats and the predicted winners
(scrape_today.py -> goal_stats.py -> predict_day.py). Then, every minute while games are live:

  a game MATCHES when it is at MIN_MINUTE_1H+ in the first half (first-half calls) or MIN_MINUTE+ in the
  second half (full-match calls) and one of its morning calls
  (winner, winner or draw, match under, a team under — see predict_day.py) is MIN_CUSHION or more
  goals from losing at the current score, priced MAX_ODDS or less. E.g. "Dortmund under 1.5 goals"
  at 0 Dortmund goals in the 80th minute, at 1.04.

All games matching at once go on one ticket, which is booked and staked (STAKE, GHS) through the
bot's own betslip script, without asking. Each game is staked at most once. Refusals:
  - stake outside config/risk.json stake_min..stake_max; balance below the stake;
  - MAX_LOSSES_PER_DAY losing tickets today (default 5): no more until tomorrow (UTC);
  - MAX_TICKETS_PER_DAY tickets already placed today (default 0 = no cap);
  - MAX_OPEN_SLIPS slips placed and not yet settled (default 3), today's and yesterday's;
  - an unclear placement: staking halts (out/live/HALT) until a person checks Bet History and
    deletes that file. Nothing is ever clicked twice.

    python3 live_monitor.py            # forever (the launchd service runs this)
    python3 live_monitor.py --once     # one pass

Env: AUTO_STAKE (1 = stake, 0 = alert only), STAKE 5, MAX_ODDS 1.04, MIN_MINUTE 80, MIN_MINUTE_1H 35, MIN_CUSHION 2,
MAX_LOSSES_PER_DAY 5, MAX_TICKETS_PER_DAY 0, INTERVAL 60, IDLE_INTERVAL 300.
"""
import datetime as dt, html, json, math, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "live"
BOT = Path("/Users/apple/Projects/claudesportybot")
AUTO_STAKE = os.environ.get("AUTO_STAKE", "1") == "1"
STAKE = float(os.environ.get("STAKE", "100"))  # the user: 100 (2026-10-16; was 5, then 40)
MAX_ODDS = float(os.environ.get("MAX_ODDS", "1.15"))
# Shadow log (the user, 2026-10-16): every bet passing every rule except the price cap, from 1.01 to
# SHADOW_MAX, is booked once (a free single code) at the first minute it qualifies and its real result
# recorded, to set the cap from data. Ones within MAX_ODDS are also staked as usual ("within_cap").
SHADOW_MAX = float(os.environ.get("SHADOW_MAX", "1.40"))
MIN_MINUTE = int(os.environ.get("MIN_MINUTE", "75"))          # full-match calls, second half
MIN_MINUTE_1H = int(os.environ.get("MIN_MINUTE_1H", "35"))    # first-half calls, first half (0 = off)
MIN_CUSHION = int(os.environ.get("MIN_CUSHION", "2"))
# The user's rule (2026-10-15): no cap on tickets; stop for the day after 5 losing tickets. 0 = no limit.
MAX_TICKETS = int(os.environ.get("MAX_TICKETS_PER_DAY", "0"))
MAX_LOSSES = int(os.environ.get("MAX_LOSSES_PER_DAY", "2"))   # the user: 2 (2026-10-16; was 5)
# At most this many slips open (placed, not yet settled) at once; the user's rule, 2026-10-15. 0 = no limit.
MAX_OPEN = int(os.environ.get("MAX_OPEN_SLIPS", "5"))
INTERVAL, IDLE = int(os.environ.get("INTERVAL", "60")), int(os.environ.get("IDLE_INTERVAL", "300"))
DOCKER = ["docker", "run", "--rm", "-v", f"{HERE}:/work", "--entrypoint", "uv", "scrapling:local", "run", "python"]
HALT = OUT / "HALT"
PRED_MIN = int(os.environ.get("PRED_MIN", "80"))   # pre-match chance (%) an outcome needs (the user: 80, 2026-10-15)

sys.path.insert(0, str(HERE))
import markets_model  # noqa: E402

sys.path.insert(0, str(BOT / "scripts"))


def log(*a):
    print(dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def today():
    return dt.datetime.now(dt.timezone.utc).date()


def container(script, *args, timeout=240):
    r = subprocess.run(DOCKER + [f"/work/{script}", *args], capture_output=True, text=True, timeout=timeout)
    line = next((l for l in reversed(r.stdout.splitlines()) if l.strip()), "")
    if r.returncode != 0 or not line:
        raise RuntimeError(f"{script} failed: {(r.stderr or r.stdout)[-300:]}")
    return line


# Which events reach Telegram (env NOTIFY, comma-separated): morning, matched, staked, won, lost, halt, balance.
# The user asked for wins only (2026-10-15); everything still goes to the log and the live page.
NOTIFY = {k.strip() for k in os.environ.get("NOTIFY", "won").split(",") if k.strip()}


def telegram(text, kind):
    if kind not in NOTIFY:
        return
    try:
        from telegram import send
        send(text)
    except Exception as e:  # noqa: BLE001
        log("telegram failed:", e)


def read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# ---- the morning analysis ----------------------------------------------------------------

REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "3"))


def ensure_predictions(day):
    """Pull the day's games, stats and calls; predictions-<day>.json marks it done. Every REFRESH_HOURS
    the pull runs again for games SportyBet listed since (one was missing at 17:33 on 2026-10-15 and
    played that evening): new games get calls, games already analysed keep theirs, so a game in
    progress never has its calls changed."""
    path = HERE / "out" / f"predictions-{day}.json"
    first = not path.exists()
    if not first and time.time() - path.stat().st_mtime < REFRESH_HOURS * 3600:
        return
    before = read(path, {})
    log(f"{'morning analysis' if first else 'refresh'} for {day}")
    container("scrape_today.py", str(day), timeout=600)
    container("goal_stats.py", str(day), timeout=1800)
    r = subprocess.run([sys.executable, str(HERE / "predict_day.py"), str(day)], capture_output=True, text=True)
    log(r.stdout.strip().splitlines()[0] if r.stdout.strip() else r.stderr[-300:])
    merged = {**read(path, {}), **before}             # earlier calls win
    path.write_text(json.dumps(merged, indent=1, ensure_ascii=False))
    if not first:
        added = [v for k, v in merged.items() if k not in before and v.get("picks")]
        log(f"refresh: {len(merged) - len(before)} new games, {len(added)} with calls")
        return
    called = [v for v in merged.values() if v.get("picks")]
    telegram(f"📋 {day}: {len(called)} games with morning calls. Staking GHS {STAKE:.0f} when a call is "
             f"{MIN_CUSHION}+ goals from losing at {MIN_MINUTE_1H}'+ (1st half) or {MIN_MINUTE}'+ at {MAX_ODDS:.2f} or less." if AUTO_STAKE else
             f"📋 {day}: {len(called)} games with morning calls (alerts only).", "morning")
    check_balance_morning()


def predictions():
    """Today's and yesterday's calls: a game after midnight UTC kicked off on the previous day.
    On-demand analyses fill in games the day's pull never saw."""
    preds = {}
    for d in (today() - dt.timedelta(days=1), today()):
        for k, v in read(HERE / "out" / f"predictions-ondemand-{d}.json", {}).items():
            preds.setdefault(k, v)
        preds.update(read(HERE / "out" / f"predictions-{d}.json", {}))
    return preds


ANALYSE_1H, ANALYSE_2H = MIN_MINUTE_1H - 5, MIN_MINUTE - 5     # 5 minutes before each window


def analyse_missing(snap, preds):
    """Run the analysis now for live games with none that are within 5 minutes of a window (or already
    in one), so no game reaches its window unanalysed. Live games have no pre-match price, so these get
    the stats calls (match under, team under, first half under) but not the winner / or-draw calls.
    A game is analysed once; one with no stats is recorded as such and not retried."""
    due = [g for g in snap["games"] if g["event"] not in preds and g["minute"] is not None
           and ((g["period"] == "H1" and g["minute"] >= ANALYSE_1H) or g["period"] == "HT"
                or (g["period"] == "H2" and g["minute"] >= ANALYSE_2H))]
    if not due:
        return preds
    import predict_day
    games = [{"event_id": g["event"], "home_team": g["home"], "away_team": g["away"], "status": "live",
              "kickoff_utc": "", "league": g["league"]} for g in due]
    (OUT / "ondemand-in.json").write_text(json.dumps(games))
    log(f"on-demand analysis: {', '.join(g['home'] + ' v ' + g['away'] for g in due)}")
    try:
        container("goal_stats.py", "--events", "/work/out/live/ondemand-in.json", "/work/out/live/ondemand-out.json", timeout=600)
        rows = read(OUT / "ondemand-out.json", [])
    except Exception as e:  # noqa: BLE001
        log("on-demand analysis failed:", e)
        return preds                     # not recorded: tried again next pass
    path = HERE / "out" / f"predictions-ondemand-{today()}.json"
    store = read(path, {})
    for r in rows:
        picks, why = predict_day.calls(r)
        model = markets_model.rates(r["stats"]) if r.get("stats") else None
        store[r["event_id"]] = {"model": {"lh": model[0], "la": model[1]} if model else None,
                                "fav": predict_day.favourite_side(r, model),
                                "match": f"{r['home_team']} v {r['away_team']}", "kickoff_utc": "", "home": r["home_team"],
                                "away": r["away_team"], "picks": picks, "side": None, "team": None,
                                "why": why or "on-demand (live: no winner calls)", "on_demand": True}
        log(f"  {r['home_team']} v {r['away_team']}: " + ("; ".join(p["name"] for p in picks) or f"no calls ({why})"))
    path.write_text(json.dumps(store, indent=1, ensure_ascii=False))
    return predictions()


# ---- matching -------------------------------------------------------------------------------

def cushion(pick, h, a):
    """Goals that would have to come, the wrong way, for this call to lose at the current score."""
    side = {"home": h, "away": a}
    if pick["kind"] in ("win", "fh_win", "dc", "hcp"):
        lead = side[pick["side"]] - side["away" if pick["side"] == "home" else "home"]
        if pick["kind"] in ("dc", "hcp") and lead < 1:
            return 0              # or-draw / handicap only ride a predicted side that is already ahead
        return max(0, lead + {"win": 0, "fh_win": 0, "dc": 1, "hcp": pick.get("k", 0)}[pick["kind"]])
    if pick["kind"] in ("total_under", "fh_under"):   # in the first half the score IS the first-half score
        return max(0, math.floor(pick["line"]) - (h + a) + 1)
    if pick["kind"] == "team_under":
        return max(0, math.floor(pick["line"]) - side[pick["side"]] + 1)
    return 0


def handicaps(picks):
    """For each predicted winner, the same side on SportyBet's handicap market (14) with a +1 and a +2
    start: leading 1:0 with +1 reads 2:0, so one late goal (2:1) still wins."""
    out = []
    for c in picks:
        if c["kind"] != "win":
            continue
        for k in (1, 2):
            spec, outcome = (f"hcp={k}:0", "1711") if c["side"] == "home" else (f"hcp=0:{k}", "1713")
            out.append({"name": f"{c['name'][:-4]} handicap +{k}", "kind": "hcp", "side": c["side"], "k": k,
                        "leg": f"14|{spec}|{outcome}"})
    return out


def window(g):
    """"1H" at MIN_MINUTE_1H+ in the first half, "FT" at MIN_MINUTE+ in the second, else None."""
    if g["minute"] is None:
        return None
    if g["period"] == "H1" and MIN_MINUTE_1H and g["minute"] >= MIN_MINUTE_1H:
        return "1H"
    if g["period"] == "H2" and g["minute"] >= MIN_MINUTE:
        return "FT"
    return None


# The user's market policy (2026-10-16):
# - unders only on one team's goals: no bet on the two teams' combined total (match / half / rest-of-match
#   over-under, total-goal ranges, exact and excluded totals, and combos or "X or" markets with a total);
# - handicaps only on the pre-match favourite (European and Asian, any period; no handicap draw).
COMBINED_TOTAL_IDS = {18, 68, 90, 900028, 21, 71, 93, 25, 548, 552, 553, 450001, 810001, 450004, 810002,
                      854, 855, 856, 857, 858, 859, 818, 819, 820}
COMBO_IDS = {35, 36, 37, 546, 547, 78, 79, 542, 543, 544, 545, 900042, 900043}
HANDICAP_IDS = {14, 65, 87, 16, 66, 88, 900035}
# Team-goal markets (over/under, ranges, exact, excluded) by the side whose goals they count.
TEAM_GOALS = {"home": {19, 69, 91, 23, 549, 450002, 450005}, "away": {20, 70, 92, 24, 550, 450003, 450006}}
TEAM_OU = {19, 69, 91, 20, 70, 92}


def allowed(o, fav, h=None, a=None):
    """None if the market policy allows this outcome, else why not. Team unders (2026-10-16): only on
    the pre-match underdog's goals, and only while the score agrees (the favourite not behind)."""
    mid, _spec, _oid, _mdesc, odesc, _price = o
    try:
        mid = int(mid)
    except (TypeError, ValueError):
        return "unknown market"
    d = (odesc or "").lower()
    if mid in COMBINED_TOTAL_IDS:
        return "combined-total market"
    if mid in COMBO_IDS and ("over" in d or "under" in d):
        return "combo with a combined total"
    if mid in HANDICAP_IDS:
        side = d.split(" ")[0]
        if not fav or side != fav:
            return "handicap not on the favourite"
    for side, ids in TEAM_GOALS.items():
        if mid in ids and not (mid in TEAM_OU and not d.startswith("under")):
            if not fav or side == fav:
                return "team under not on the underdog"
            if h is not None:
                fg, dg = (h, a) if fav == "home" else (a, h)
                if fg < dg:
                    return "underdog ahead: the score does not back the call"
    return None


_sims = {}       # event -> simulated matches from its model (built once per process)
_probs = {}      # (event, market, specifier, outcome) -> pre-match chance


def chance(event, model, o):
    key = (event, o[0], o[1], o[2])
    if key not in _probs:
        r = markets_model.rule(o[0], o[1], o[4], o[3])
        if event not in _sims:
            _sims[event] = markets_model.simulate(model["lh"], model["la"])
        _probs[key] = markets_model.prob(r[0], _sims[event]) if r else None
    return _probs[key]


SHADOWS = []     # filled by matches(): candidates priced above the cap, for the shadow log


def matches(snap, preds, staked):
    """Every open outcome on every analysed live game, on any market goals settle (markets_model):
    it qualifies at MAX_ODDS or less when MIN_CUSHION+ further goals would be needed to lose it and
    the game's analysis gave it PRED_MIN%+ before kick-off. First-half markets in the first-half
    window, full-match markets in the second-half window. One leg per game per half: the best price,
    then the bigger cushion."""
    out = []
    for g in snap["games"]:
        p = preds.get(g["event"])
        half = window(g)
        if not p or not p.get("model") or not half or f"{g['event']}|{half}" in staked:
            continue
        try:
            h, a = map(int, g["score"].split(":"))
        except (AttributeError, ValueError):
            continue
        halves = list(g.get("halves") or [])
        if half == "1H":
            halves, now = [halves[0] if halves else (h, a)], 1
        else:
            if len(halves) < 2:   # no per-half split: count every goal as first half, the order test still runs
                halves = [(h, a), (0, 0)]
            halves, now = halves[:2], 2
        ok = []
        for o in g.get("outcomes") or []:
            mid, spec, oid, mdesc, odesc, price = o
            if price > SHADOW_MAX or allowed(o, p.get("fav"), h, a):
                continue
            r = markets_model.rule(mid, spec, odesc, mdesc)
            if not r or r[1] != half:
                continue
            need = markets_model.cushion(r[0], halves, now)
            if need < MIN_CUSHION:
                continue
            pc = chance(g["event"], p["model"], o)
            if pc is None or pc * 100 < PRED_MIN:
                continue
            by_side = markets_model.cushion_by_side(r[0], halves, now)
            danger = [sd for sd, n in zip(("home", "away"), by_side) if n <= markets_model.MAX_EXTRA]
            name = f"{mdesc}: {odesc}" if mdesc else odesc
            cand = {"name": name, "price": price, "cushion": need, "pre_match": round(pc * 100), "danger": danger,
                    "leg": f"{g['event']}/{mid}/{spec}/{oid}"}
            SHADOWS.append({"event": g["event"], "half": half, "minute": g["minute"], "score": g["score"],
                            "match": f"{g['home']} v {g['away']}", "within_cap": price <= MAX_ODDS, **cand})
            if price <= MAX_ODDS:
                ok.append(cand)
        if ok:
            ok.sort(key=lambda c: (-c["price"], -c["cushion"]))
            out.append({**g, "half": half, "team": ok[0]["name"], "pick": ok[0], "alts": ok})
    return out


# ---- the live check (Sportradar's live match stats, just before staking) --------------------
LIVE_CHECK = os.environ.get("LIVE_CHECK", "1") == "1"
LIVE_RECENT_SOT = int(os.environ.get("LIVE_RECENT_SOT", "2"))     # danger side's shots on target, last 10'
LIVE_DA_PCT = int(os.environ.get("LIVE_DA_PCT", "70"))            # danger side's dangerous-attack share


def live_stats(events, minute):
    ids = sorted({e for e in events if len(e) < 20})
    if not ids:
        return {}
    try:
        return json.loads(container("live_stats.py", str(minute), *ids, timeout=120))
    except Exception as e:  # noqa: BLE001
        log("live stats failed:", e)
        return {}


FS_DOMINANT_SOT = int(os.environ.get("FS_DOMINANT_SOT", "4"))   # Flashscore with no history: whole-match check
_history = {}    # event -> [(minute, [sot home, sot away])], from either source, for recent pressure


def remember(event, minute, stats):
    if stats and minute is not None:
        _history.setdefault(event, []).append((minute, list(stats["sot"])))


def recent_from_history(event, minute, stats):
    """Shots on target since the reading closest to 10 minutes ago (needs one at least 8 minutes old)."""
    old = [(m, s) for m, s in _history.get(event, []) if m <= minute - 8]
    if not old or not stats:
        return None
    _, then = max(old, key=lambda x: x[0])
    return {"sot": [max(0, stats["sot"][0] - then[0]), max(0, stats["sot"][1] - then[1])], "shots": None}


def flashscore(games):
    """The fallback source for games Sportradar does not cover (one browser run for all of them)."""
    if not games:
        return {}
    (OUT / "fs-in.json").write_text(json.dumps([{"event": g["event"], "home": g["home"], "away": g["away"],
                                                 "score": g["score"]} for g in games]))
    try:
        got = json.loads(container("flashscore_stats.py", "/work/out/live/fs-in.json", timeout=300))
    except Exception as e:  # noqa: BLE001
        log("flashscore failed:", e)
        return {}
    for g in games:
        log(f"flashscore: {g['home']} v {g['away']} -> " + (got.get(g["event"]) or {}).get("flashscore", "no confident match"))
    return {k: v for k, v in got.items() if v}


def live_block(stats, danger):
    """Why the live game argues against the bet, or None. The danger side is the team whose goals lose it."""
    if not stats:
        # The user (2026-10-16): stake only when the morning analysis, the live analysis and the score all
        # agree, so a game without live stats is not staked.
        return "no live stats for this game"
    idx = {"home": 0, "away": 1}
    for d in danger:
        i, o = idx[d], 1 - idx[d]
        if stats["red"][o] > stats["red"][i]:
            return f"{d} has a man advantage (red card)"
        if stats.get("recent"):
            if stats["recent"]["sot"][i] >= LIVE_RECENT_SOT:
                return f"{d} {stats['recent']['sot'][i]} shots on target in the last 10'"
        elif stats["sot"][i] >= FS_DOMINANT_SOT and stats["sot"][i] >= 2 * stats["sot"][o]:
            # No timeline and no reading from 10 minutes ago: judge the whole match instead.
            return f"{d} {stats['sot'][i]} shots on target to {stats['sot'][o]} (no recent-minutes data)"
        if stats.get("da_pct") and stats["da_pct"][i] >= LIVE_DA_PCT:
            return f"{d} {stats['da_pct'][i]}% of dangerous attacks"
    return None


def live_check(group):
    """Keep, per game, the best candidate the live stats don't argue against; note every shadow's verdict."""
    if not LIVE_CHECK:
        return group
    stats = live_stats([g["event"] for g in group] + [c["event"] for c in SHADOWS],
                       max([g["minute"] for g in group] + [c["minute"] for c in SHADOWS] + [0]))
    # Sportradar first; Flashscore for games it does not cover (the user, 2026-10-16).
    stats.update(flashscore([g for g in group if not stats.get(g["event"])]))
    minutes = {g["event"]: g["minute"] for g in group}
    minutes.update({c["event"]: c["minute"] for c in SHADOWS})
    for ev, st in stats.items():
        if st and st.get("source") == "flashscore":
            st["recent"] = recent_from_history(ev, minutes.get(ev, 0), st)
        remember(ev, minutes.get(ev), st)
    for c in SHADOWS:
        c["live"] = stats.get(c["event"])
        c["live_block"] = live_block(c["live"], c["danger"])
    kept = []
    for g in group:
        st = stats.get(g["event"])
        for c in g["alts"]:
            why = live_block(st, c["danger"])
            if not why:
                kept.append({**g, "team": c["name"], "pick": {**c, "live": st}})
                break
            log(f"live check: {g['home']} v {g['away']} {g['score']} {g['minute']}' — {c['name']} @ {c['price']:.2f} not staked: {why}")
    return kept


# ---- batching (the user, 2026-10-16: bigger odds per 5 cedis) -------------------------------------
HOLD_MINUTES = float(os.environ.get("HOLD_MINUTES", "1.5"))   # wait at most this long for sibling games (the user: 1.5)
SIBLING_KICKOFF_MIN = 5                                        # kicked off within 5 minutes of each other
_held = {}       # event|half -> when it first qualified while waiting for siblings


def pending_siblings(group, snap, preds, staked):
    """Analysed games that kicked off with a qualifying one and are close to (or in) the same window but
    have not qualified yet: worth a short wait so they can share the slip."""
    in_group = {g["event"] for g in group}
    out = []
    for g in group:
        k = g.get("kickoff")
        if not k:
            continue
        for s in snap["games"]:
            if s["event"] in in_group or s["event"] in {x["event"] for x in out} or not s.get("kickoff"):
                continue
            if abs(s["kickoff"] - k) > SIBLING_KICKOFF_MIN * 60_000:
                continue
            p = preds.get(s["event"]) or {}
            if not p.get("model") or f"{s['event']}|{g['half']}" in staked or s["minute"] is None:
                continue
            near = ((g["half"] == "1H" and s["period"] == "H1" and s["minute"] >= MIN_MINUTE_1H - 5) or
                    (g["half"] == "FT" and s["period"] == "H2" and s["minute"] >= MIN_MINUTE - 5))
            if near:
                out.append(s)
    return out


def hold(group, snap, preds, staked):
    """True while the qualifying games should wait for siblings (at most HOLD_MINUTES from the first)."""
    now = time.time()
    keys = [f"{g['event']}|{g['half']}" for g in group]
    for k in keys:
        _held.setdefault(k, now)
    waiting = pending_siblings(group, snap, preds, staked)
    age = now - min(_held[k] for k in keys)
    if waiting and age < HOLD_MINUTES * 60:
        log(f"holding {', '.join(g['home'] + ' v ' + g['away'] for g in group)} for "
            f"{', '.join(s['home'] + ' v ' + s['away'] for s in waiting)} ({HOLD_MINUTES * 60 - age:.0f}s left)")
        return True
    for k in keys:
        _held.pop(k, None)
    return False


# ---- guards ---------------------------------------------------------------------------------

def tickets_file(day):
    return OUT / f"tickets-{day}.json"


def settle(tickets):
    """Fill in won/lost for tickets whose games have ended (read from the booking code)."""
    for t in tickets:
        if t.get("result") or t.get("state") != "placed":
            continue
        try:
            legs = json.loads(container("book_live.py", "--status", t["code"], timeout=120))
        except Exception as e:  # noqa: BLE001
            log("status failed:", t["code"], e)
            continue
        ended = [l for l in legs if l["status"] in (3, 4)]
        if any(l["winning"] == 0 for l in ended):
            t["result"] = "lost"
        elif len(ended) == len(legs) and all(l["winning"] == 1 for l in legs):
            t["result"] = "won"
        if t.get("result"):
            telegram(f"{'✅' if t['result'] == 'won' else '❌'} Ticket {t['code']} {t['result'].upper()} "
                     f"(GHS {t['stake']:.2f} @ {t['odds']:.2f})", t["result"])
    return tickets


def refusal(tickets, open_now=0):
    import risk
    cfg = risk.load_config()
    if HALT.exists():
        return f"halted: {HALT.read_text().strip()} (check Bet History, then delete {HALT})"
    if not cfg["stake_min"] <= STAKE <= cfg["stake_max"]:
        return f"stake GHS {STAKE} outside config/risk.json {cfg['stake_min']}-{cfg['stake_max']}"
    placed = [t for t in tickets if t.get("state") == "placed"]
    if MAX_TICKETS and len(placed) >= MAX_TICKETS:
        return f"{len(placed)} tickets already today (MAX_TICKETS_PER_DAY {MAX_TICKETS})"
    if MAX_OPEN and open_now >= MAX_OPEN:
        return f"{open_now} slips still open (MAX_OPEN_SLIPS {MAX_OPEN}); waiting for one to settle"
    lost = sum(1 for t in placed if t.get("result") == "lost")
    if MAX_LOSSES and lost >= MAX_LOSSES:
        return f"{lost} losing tickets today: stopped until tomorrow (UTC) (MAX_LOSSES_PER_DAY {MAX_LOSSES})"
    bal = risk.balance_now()
    if bal is None:
        return "balance unreadable (session expired? run `npm run login` in claudesportybot)"
    if bal < STAKE:
        return f"balance GHS {bal:.2f} below the GHS {STAKE:.2f} stake"
    return None


def balance_alert(why, bal=None):
    """Tell the user the balance can't cover a stake: at most once an hour until it can."""
    mark = OUT / "balance_alerted"
    try:
        if time.time() - mark.stat().st_mtime < 3600:
            return
    except OSError:
        pass
    mark.touch()
    telegram(f"💸 Insufficient balance: {why}.\nTop up SportyBet to keep live staking going "
             f"(GHS {STAKE:.2f} per slip).", "balance")


def check_balance_morning():
    import risk
    bal = risk.balance_now()
    if bal is not None and bal < STAKE:
        balance_alert(f"SportyBet shows GHS {bal:.2f}, below the GHS {STAKE:.2f} stake", bal)


# ---- staking --------------------------------------------------------------------------------

def stake(group, tickets):
    legs = [g["pick"]["leg"] for g in group]
    odds = 1.0
    for g in group:
        odds *= g["pick"]["price"]
    desc = "; ".join(f"{g['minute']}' {g['home']} {g['score']} {g['away']} → {g['pick']['name']} @ {g['pick']['price']:.2f}"
                     f" ({g['pick']['cushion']} goals to lose, {g['pick']['pre_match']}% pre-match)" for g in group)
    code = container("book_live.py", *legs, timeout=120)
    # The cushion was counted from the score at scan time. A goal since then can leave a leg one goal
    # from losing, so re-read every leg's score through the booked code and stake only if none moved.
    now = {l["event"]: l["score"] for l in json.loads(container("book_live.py", "--status", code, timeout=120))}
    moved = [g for g in group if now.get(g["event"]) != g["score"]]
    if moved:
        log(f"{code}: not staked, score changed since the scan: " +
            ", ".join(f"{g['home']} {g['score']}→{now.get(g['event'])}" for g in moved))
        return None
    t = {"code": code, "stake": STAKE, "odds": round(odds, 2), "events": [g["event"] for g in group], "keys": [f"{g['event']}|{g['half']}" for g in group],
         "games": desc, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "state": "attempted"}
    tickets.append(t)
    tickets_file(today()).write_text(json.dumps(tickets, indent=1))   # recorded before any click
    log(f"STAKING {code}: {desc}")
    try:
        r = subprocess.run(["npm", "run", "-s", "stage", "--", code, "--stake", f"{STAKE:g}", "--place", "--headless"],
                           cwd=BOT, capture_output=True, text=True, timeout=420)
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e), 3
    with open(OUT / "stage.log", "a") as f:
        f.write(f"\n==== {t['at']} {code} exit {rc}\n{out}\n")
    if rc == 0:
        t["state"] = "placed"
        telegram(f"💰 Staked GHS {STAKE:.2f} on {code} (total {t['odds']:.2f}, returns ~GHS {STAKE * t['odds']:.2f})\n{desc}", "staked")
    elif "Nothing was placed" in out:
        t["state"] = "not_placed"
        t["why"] = out.strip().splitlines()[-1][:200]
        log("not placed:", t["why"])
    else:
        t["state"] = "unclear"
        HALT.write_text(f"{t['at']} {code}: placement result unclear (stage exit {rc})")
        telegram(f"⚠️ Ticket {code}: placement result UNCLEAR. Auto-staking is halted. Check Bet History, "
                 f"then delete {HALT} to resume.\n{desc}", "halt")
    tickets_file(today()).write_text(json.dumps(tickets, indent=1))
    log(f"{code}: {t['state']}")
    return t


# ---- shadow log -------------------------------------------------------------------------------

def shadow_file(day):
    return OUT / f"shadow-{day}.json"


def shadow_record(day):
    """Book each new shadow candidate once (its own single code) and keep it for settling."""
    if not SHADOWS:
        return
    rows = read(shadow_file(day), [])
    seen = {(r["leg"]) for d in (day - dt.timedelta(days=1), day) for r in read(shadow_file(d), [])}
    for c in SHADOWS:
        if c["leg"] in seen:
            continue
        seen.add(c["leg"])
        try:
            c["code"] = container("book_live.py", c["leg"], timeout=120)
        except Exception as e:  # noqa: BLE001
            log("shadow booking failed:", e)
            continue
        c["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        c["result"] = None
        rows.append(c)
        log(f"shadow: {c['match']} {c['minute']}' {c['score']} → {c['name']} @ {c['price']:.2f} "
            f"({c['cushion']} to lose, {c['pre_match']}%) code {c['code']}")
    shadow_file(day).write_text(json.dumps(rows, indent=1))


def shadow_settle(day):
    """Read results for shadow legs whose games have ended (every 5 minutes, one container run)."""
    mark = OUT / "shadow_checked"
    try:
        if time.time() - mark.stat().st_mtime < 300:
            return
    except OSError:
        pass
    mark.touch()
    for d in (day - dt.timedelta(days=1), day):
        rows = read(shadow_file(d), [])
        open_ = [r for r in rows if r.get("result") is None and r.get("code")]
        if not open_:
            continue
        try:
            st = json.loads(container("book_live.py", "--status-many", *[r["code"] for r in open_], timeout=300))
        except Exception as e:  # noqa: BLE001
            log("shadow settle failed:", e)
            continue
        for r in open_:
            legs = st.get(r["code"])
            if not isinstance(legs, list) or not legs:
                continue
            leg = legs[0]
            if leg["status"] in (3, 4) and leg["winning"] in (0, 1):
                r["result"] = "won" if leg["winning"] == 1 else "lost"
                r["final"] = leg.get("score")
        shadow_file(d).write_text(json.dumps(rows, indent=1))


# ---- the page -------------------------------------------------------------------------------

def page(snap, preds, tickets, note):
    e = html.escape
    rows = []
    for g in sorted(snap["games"], key=lambda g: -(g["minute"] or 0)):
        p = preds.get(g["event"]) or {}
        try:
            h, a = map(int, (g["score"] or "").split(":"))
        except ValueError:
            h = a = None
        bits = []
        for c in p.get("picks") or []:
            pr = g.get("prices", {}).get(c["leg"])
            need = cushion(c, h, a) if h is not None else None
            hot = pr is not None and pr <= MAX_ODDS and need is not None and need >= MIN_CUSHION
            bits.append(f"<span class='pk{' hot' if hot else ''}'>{e(c['name'])} <b>{pr:.2f}</b>" if pr else
                        f"<span class=pk>{e(c['name'])} <small>closed</small>")
            bits[-1] += f"<small> · {need} to lose</small></span>" if need is not None else "</span>"
        call = " ".join(bits) or "<small>no call</small>"
        price = ""
        late = window(g) is not None
        rows.append(f"<tr class='{'late' if late else ''}'><td>{g['minute'] if g['minute'] is not None else '–'}'</td>"
                    f"<td class=sc>{e(g['score'] or '')}</td><td>{e(g['home'])}<br>{e(g['away'])}<br><small>{e(g['league'])}</small></td>"
                    f"<td>{call}</td><td>{price}</td></tr>")
    trows = "".join(f"<tr><td>{e(t['at'][11:16])}</td><td>{e(t['code'])}</td><td>{e(t['state'])}{(' · ' + e(t['result'])) if t.get('result') else ''}</td>"
                    f"<td>{t['odds']:.2f}</td><td><small>{e(t['games'])}</small></td></tr>" for t in tickets) or "<tr><td colspan=5><small>none today</small></td></tr>"
    at = dt.datetime.fromtimestamp(snap["at"], dt.timezone.utc).strftime("%H:%M:%S UTC")
    (OUT / "index.html").write_text(f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=30>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Live games</title>
<style>body{{font:14px system-ui;margin:16px;background:#fff;color:#111}}table{{border-collapse:collapse;width:100%;margin-bottom:20px}}
td{{padding:6px;border-bottom:1px solid #e5e5e5;vertical-align:top}}.sc{{font-size:18px;font-weight:700}}tr.late{{background:#fff7e0}}
small{{color:#777}}.note{{padding:8px 12px;background:#f4f4f4;border-radius:8px}}
.pk{{display:inline-block;margin:0 6px 4px 0;padding:2px 8px;border-radius:12px;background:#f0f0f0}}.pk.hot{{background:#c8f0d0}}</style>
<h2>Live football — {len(snap['games'])} games</h2>
<p class=note>{'Auto-staking' if AUTO_STAKE else 'Alerts only'}: a morning call {MIN_CUSHION}+ goals from losing — 1st-half calls at {MIN_MINUTE_1H}'+, full-match calls at {MIN_MINUTE}'+ — priced ≤ {MAX_ODDS:.2f} (green), GHS {STAKE:.0f}.
{('<br><b>' + e(note) + '</b>') if note else ''}<br><small>Updated {at} · refreshes every 30 s</small></p>
<h3>Today's tickets</h3><table>{trows}</table>
<h3>Live</h3><table><tr><td></td><td></td><td></td><td><small>morning calls · live price · goals to lose</small></td><td></td></tr>{''.join(rows)}</table>""")


# ---- the loop -------------------------------------------------------------------------------

def once():
    day = today()
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        ensure_predictions(day)
    except Exception as e:  # noqa: BLE001
        log("morning analysis failed:", e)
    preds = predictions()
    snap = json.loads(container("live_scan.py"))
    preds = analyse_missing(snap, preds)
    (OUT / "latest.json").write_text(json.dumps(snap, indent=1))
    with open(OUT / f"{day}.jsonl", "a") as f:
        f.write(json.dumps(snap) + "\n")

    yday = day - dt.timedelta(days=1)
    earlier = read(tickets_file(yday), [])
    if any(t.get("state") == "placed" and not t.get("result") for t in earlier):
        tickets_file(yday).write_text(json.dumps(settle(earlier), indent=1))   # late slips settle after midnight
    tickets = settle(read(tickets_file(day), []))
    tickets_file(day).write_text(json.dumps(tickets, indent=1))
    open_now = sum(1 for t in earlier + tickets if t.get("state") == "placed" and not t.get("result"))
    # Staked game+half pairs: a game can be bet once in the first half and once at the end.
    # Tickets from before halves were tracked count as full-match ("FT").
    staked = {k for d in (day - dt.timedelta(days=1), day) for t in read(tickets_file(d), [])
              if t.get("state") in ("placed", "unclear", "attempted")
              for k in (t.get("keys") or [f"{ev}|FT" for ev in t["events"]])}
    SHADOWS.clear()
    group = live_check(matches(snap, preds, staked))
    try:
        shadow_record(day)
        shadow_settle(day)
    except Exception as e:  # noqa: BLE001
        log("shadow log failed:", e)
    note = ""
    if group and AUTO_STAKE and hold(group, snap, preds, staked):
        group = []                        # re-checked next pass with any siblings that qualify meanwhile
    if group:
        names = ", ".join(f"{g['pick']['name']} ({g['home']} {g['score']} {g['away']}, {g['minute']}')" for g in group)
        why = refusal(tickets, open_now) if AUTO_STAKE else "alerts only (AUTO_STAKE=0)"
        if why and why.startswith("balance GHS"):
            balance_alert(f"{why} — matched {names}")
        if why:
            note = f"Matched {names} — not staked: {why}"
            log(note)
            key = OUT / "refused.json"
            seen = read(key, [])
            new = [g["event"] for g in group if g["event"] not in seen]
            if new:
                telegram(f"🔎 Matched: {names}\nNot staked: {why}", "matched")
                key.write_text(json.dumps(seen + new))
        else:
            stake(group, tickets)
    page(snap, preds, tickets, note)
    late = sum(1 for g in snap["games"] if (g["minute"] or 0) >= MIN_MINUTE)
    with_call = sum(1 for g in snap["games"] if (preds.get(g["event"]) or {}).get("picks"))
    log(f"{len(snap['games'])} live, {with_call} with a morning call, {late} at {MIN_MINUTE}'+, {len(group)} matching")
    return len(snap["games"])


def main():
    log(f"live monitor: {'AUTO-STAKE' if AUTO_STAKE else 'alerts only'} GHS {STAKE:g} · cushion {MIN_CUSHION}+ goals · "
        f"1H {MIN_MINUTE_1H}'+ / FT {MIN_MINUTE}'+ · price ≤ {MAX_ODDS} · max {MAX_OPEN} open slips · stop after {MAX_LOSSES} losses/day"
        f"{f' · max {MAX_TICKETS} tickets/day' if MAX_TICKETS else ' · no ticket cap'}")
    while True:
        try:
            live = once()
        except Exception as e:  # noqa: BLE001
            log("pass failed:", e)
            live = 1
        if "--once" in sys.argv:
            return
        time.sleep(INTERVAL if live else IDLE)


if __name__ == "__main__":
    main()
