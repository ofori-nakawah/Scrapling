"""Client for betslip_daemon.py (run inside the worker container). Same arguments and exit codes as
claudesportybot's stage-bet.js, so the live monitor treats both alike:

    python betslip.py CODE --stake 100 [--min-odds X] [--max-odds X] [--place | --to-confirm]
    python betslip.py --health

exit 0 placed (or dry run / confirm-panel test OK) · 1 nothing placed · 3 unclear · 4 daemon not running
--to-confirm: clicks Place Bet, checks the confirm panel names the stake, then stops without confirming.
"""
import argparse, json, sys
from urllib.error import URLError
from urllib.request import Request, urlopen

URL = "http://127.0.0.1:8765"


def main():
    if sys.argv[1:2] == ["--health"]:
        try:
            print(urlopen(URL + "/health", timeout=5).read().decode())
            return 0
        except (URLError, OSError):
            print("betslip daemon not running")
            return 4
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--stake", type=float, required=True)
    ap.add_argument("--min-odds", type=float)
    ap.add_argument("--max-odds", type=float)
    ap.add_argument("--place", action="store_true")
    ap.add_argument("--to-confirm", action="store_true")
    a = ap.parse_args()
    if not 1 <= a.stake <= 500:
        print("--stake must be between GHS 1 and GHS 500. Nothing was placed.")
        return 1
    body = json.dumps({"code": a.code.upper(), "stake": a.stake, "min_odds": a.min_odds, "max_odds": a.max_odds,
                       "place": a.place, "to_confirm": a.to_confirm}).encode()
    try:
        res = json.loads(urlopen(Request(URL + "/stake", data=body, headers={"content-type": "application/json"}),
                                 timeout=300).read())
    except (URLError, OSError) as e:
        # The request may have reached the daemon: for a real placement that is not a safe "no".
        if a.place:
            print(f"betslip daemon connection lost ({e}). Result unclear: check Bet History.")
            return 3
        print(f"betslip daemon not reachable ({e}). Nothing was placed.")
        return 4
    print("\n".join(res.get("lines") or []))
    print(f"({res.get('seconds')} s in the warm betslip)")
    return int(res.get("exit", 3))


if __name__ == "__main__":
    sys.exit(main())
