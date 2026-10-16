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
    lost = [r for r in settled if r["result"] == "lost"]
    if lost:
        print("\nlosses:")
        for r in lost[-10:]:
            print(f"  {r['at'][:16]} {r['match']} {r['minute']}' {r['score']} → {r['name']} @ {r['price']:.2f}, final {r.get('final')}")


if __name__ == "__main__":
    main()
