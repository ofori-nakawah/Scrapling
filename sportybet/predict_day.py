#!/usr/bin/env python3
"""The morning calls for each of a day's games, on any market the score can settle.

    python3 predict_day.py [YYYY-MM-DD]      # reads out/goals-<date>.json, writes out/predictions-<date>.json

Per game, every call the analysis supports (each one is a SportyBet leg "market|specifier|outcome"):
  - <team> win        the pre-match favourite at FAV_MAX_ODDS or shorter whose form (goal difference per
                      game over the last 10) beats the opponent's
  - <team> or draw    the same favourite at DC_MAX_ODDS or shorter, form agreeing
  - Under 2.5 / 3.5   the two sides' last-10 rate of staying under the line, averaged, PRED_MIN% or more
  - <team> under 0.5 / 1.5 / 2.5 goals
                      that team scoring no more than the line in its last 10, averaged with the opponent
                      conceding no more than it in theirs, PRED_MIN% or more
A game with no supported call gets none, and the live routine never bets it.
"""
import datetime as dt, json, math, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAV_MAX_ODDS = float(os.environ.get("FAV_MAX_ODDS", "2.20"))
DC_MAX_ODDS = float(os.environ.get("DC_MAX_ODDS", "2.60"))
PRED_MIN = int(os.environ.get("PRED_MIN", "70"))


def favourite(g, s):
    """(side, price, why) for the favourite whose form agrees, else (None, None, why)."""
    if g.get("status") != "upcoming":
        return None, None, "already live when analysed (no pre-match price)"
    h, a = g.get("home"), g.get("away")
    if not h or not a:
        return None, None, "no 1X2 price"
    side, price = ("home", h) if h < a else ("away", a)
    other = "away" if side == "home" else "home"
    gd = {k: s[k]["avg_scored"] - s[k]["avg_conceded"] for k in ("home", "away")}
    if gd[side] <= gd[other]:
        return None, price, f"form disagrees ({side} GD {gd[side]:+.1f} v {gd[other]:+.1f})"
    return side, price, f"favourite @ {price:.2f}, form GD {gd[side]:+.1f} v {gd[other]:+.1f} per game"


def calls(g):
    s = g.get("stats")
    if not s:
        return [], "no stats"
    team = {"home": g["home_team"], "away": g["away_team"]}
    out = []
    side, price, why = favourite(g, s)
    if side and price <= FAV_MAX_ODDS:
        out.append({"name": f"{team[side]} win", "kind": "win", "side": side,
                    "leg": f"1||{'1' if side == 'home' else '3'}", "why": why})
    if side and price <= DC_MAX_ODDS:
        out.append({"name": f"{team[side]} or draw", "kind": "dc", "side": side,
                    "leg": f"10||{'9' if side == 'home' else '11'}", "why": why})
    for line in (2.5, 3.5):
        o = s["blend"].get(f"o{line}")
        if o is not None and 100 - o >= PRED_MIN:
            out.append({"name": f"Under {line}", "kind": "total_under", "line": line,
                        "leg": f"18|total={line}|13", "why": f"last-10 under {line}: {100 - o}%"})
    for sd, other in (("home", "away"), ("away", "home")):
        for line in (0.5, 1.5, 2.5):
            n = math.floor(line)
            a, b = s[sd].get(f"scored_le{n}"), s[other].get(f"conceded_le{n}")
            if a is None or b is None:
                continue
            rate = round((a + b) / 2)
            if rate >= PRED_MIN:
                out.append({"name": f"{team[sd]} under {line} goals", "kind": "team_under", "side": sd, "line": line,
                            "leg": f"{'19' if sd == 'home' else '20'}|total={line}|13",
                            "why": f"{team[sd]} scored ≤{n} in {a}%, {team[other]} conceded ≤{n} in {b}%"})
                break                     # the lowest line that holds is the call for this team
    return out, why if not out else ""


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else str(dt.datetime.now(dt.timezone.utc).date())
    games = json.load(open(HERE / "out" / f"goals-{day}.json"))
    out = {}
    for g in games:
        picks, why = calls(g)
        win = next((p for p in picks if p["kind"] == "win"), None)
        out[g["event_id"]] = {"match": f"{g['home_team']} v {g['away_team']}", "kickoff_utc": g["kickoff_utc"],
                              "home": g["home_team"], "away": g["away_team"], "picks": picks,
                              "side": win["side"] if win else None, "team": win["name"][:-4] if win else None,
                              "why": why}
    (HERE / "out" / f"predictions-{day}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    called = [v for v in out.values() if v["picks"]]
    print(f"{len(called)}/{len(out)} games have calls ({sum(len(v['picks']) for v in called)} in all)")
    for v in called:
        print(f"  {v['kickoff_utc']} {v['match']}: " + "; ".join(p["name"] for p in v["picks"]))


if __name__ == "__main__":
    main()
