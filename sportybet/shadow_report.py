#!/usr/bin/env python3
"""Win rate by price band and by minute, from the shadow log: every bet that passed the rules (cushion,
pre-match chance, window) from 1.01 to 1.40, booked once at the first minute it qualified. Bets within the
cap were also staked; the log measures every band the same way either way.

    python3 shadow_report.py            # every day on file
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "live"
BANDS = [(1.0, 1.02), (1.02, 1.05), (1.05, 1.08), (1.08, 1.12), (1.12, 1.16), (1.16, 1.20),
         (1.20, 1.25), (1.25, 1.30), (1.30, 1.35), (1.35, 1.40)]


STAKE = 100           # the live monitor's stake (GHS), for the profit column
MIN_RESULTS = 30


def bar_finder(settled):
    """Every pre-match bar x live-score bar on the settled shadow bets: what each would have staked, its win rate
    and profit at STAKE a bet. The best bars are the most profitable pair with MIN_RESULTS or more."""
    rows = [r for r in settled if r.get("pre_match") is not None]
    scored = [r for r in rows if r.get("live_score") is not None]
    print(f"\nbar finder: {len(rows)} settled bets with a pre-match chance, {len(scored)} also with a live score")
    if not rows:
        return
    use_live = len(scored) >= MIN_RESULTS
    pool = scored if use_live else rows
    if not use_live:
        print(f"  (live-score bars wait for {MIN_RESULTS}+ bets carrying a live score; pre-match bars only for now)")
    results = []
    for pre in (60, 65, 70, 75, 80, 85):
        for live in ((50, 60, 70, 80, 90) if use_live else (None,)):
            b = [r for r in pool if r["pre_match"] >= pre and (live is None or r["live_score"] >= live)]
            if not b:
                continue
            won = sum(r["result"] == "won" for r in b)
            profit = sum(STAKE * (r["price"] - 1) if r["result"] == "won" else -STAKE for r in b)
            results.append((profit, pre, live, len(b), won))
    print(f"  {'pre-match':>9} {'live':>5} {'bets':>5} {'won':>4} {'win %':>6} {'profit GHS':>11}")
    for profit, pre, live, n, won in sorted(results, key=lambda x: (x[2] or 0, x[1])):
        print(f"  {pre:>8}% {('-' if live is None else live):>5} {n:>5} {won:>4} {100 * won / n:>5.1f}% {profit:>11.2f}"
              f"{'' if n >= MIN_RESULTS else '  (too few)'}")
    ok = [x for x in results if x[3] >= MIN_RESULTS]
    if ok:
        profit, pre, live, n, won = max(ok)
        print(f"  BEST: pre-match {pre}%" + (f", live score {live}" if live is not None else "") +
              f" -> {n} bets, {100 * won / n:.1f}% won, GHS {profit:.2f}")
    else:
        print(f"  BEST: not yet: no pair has {MIN_RESULTS} settled bets")


def main():
    rows = [r for f in sorted(OUT.glob("shadow-*.json")) for r in json.loads(f.read_text())]
    settled = [r for r in rows if r.get("result")]
    print(f"shadow log: {len(rows)} candidates, {len(settled)} settled\n")
    print(f"{'price':>11}  {'n':>4}  {'won':>4}  {'win %':>6}  {'break-even %':>12}  verdict")
    for lo, hi in BANDS:
        b = [r for r in settled if lo < r["price"] <= hi]
        if not b:
            continue
        won = sum(r["result"] == "won" for r in b)
        rate = 100 * won / len(b)
        mid = (lo + hi) / 2
        need = 100 / mid
        verdict = "profitable" if rate > need else "losing"
        print(f"{lo:.2f}-{hi:.2f}  {len(b):>4}  {won:>4}  {rate:>5.1f}%  {need:>11.1f}%  {verdict}{'  (too few to judge)' if len(b) < 30 else ''}")
    print(f"\n{'window':>11}  {'n':>4}  {'won':>4}  {'win %':>6}")
    for name, test in (("1st half", lambda r: r["half"] == "1H"),
                       ("73-79'", lambda r: r["half"] == "FT" and r["minute"] < 80),
                       ("80'+", lambda r: r["half"] == "FT" and r["minute"] >= 80)):
        b = [r for r in settled if test(r)]
        if b:
            won = sum(r["result"] == "won" for r in b)
            print(f"{name:>11}  {len(b):>4}  {won:>4}  {100 * won / len(b):>5.1f}%")
    bar_finder(settled)
    lost = [r for r in settled if r["result"] == "lost"]
    if lost:
        print("\nlosses:")
        for r in lost[-10:]:
            print(f"  {r['at'][:16]} {r['match']} {r['minute']}' {r['score']} → {r['name']} @ {r['price']:.2f}, final {r.get('final')}")


if __name__ == "__main__":
    main()
