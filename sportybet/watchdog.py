#!/usr/bin/env python3
"""Watchdog for the live monitor (launchd com.scrapling.watchdog, every 5 minutes).

- Heartbeat: the monitor touches out/live/heartbeat on every pass. Stale for STALE_MINUTES -> Telegram
  alert, saying whether the monitor service, Docker or the network looks like the cause.
- SportyBet session: if the balance can't be read, run the bot's own keep-alive once (no password);
  alert only if it still can't be read.
Each alert repeats at most once an hour while the problem lasts, and a recovery message follows.
"""
import json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "live"
BOT = Path("/Users/apple/Projects/claudesportybot")
STATE = OUT / "watchdog.json"
STALE_MINUTES = float(os.environ.get("STALE_MINUTES", "10"))
sys.path.insert(0, str(BOT / "scripts"))


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), *a, flush=True)


def telegram(text):
    try:
        from telegram import send
        send(text)
    except Exception as e:  # noqa: BLE001
        log("telegram failed:", e)


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)[-300:]
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def alert(state, key, problem, text):
    """Alert on a new problem (then hourly); send one 'back to normal' when it clears."""
    s = state.setdefault(key, {"bad": False, "last": 0})
    if problem:
        if not s["bad"] or time.time() - s["last"] >= 3600:
            telegram(text)
            s["last"] = time.time()
            log("ALERT", key, text.replace("\n", " | "))
        s["bad"] = True
    elif s["bad"]:
        telegram(f"✅ {key}: back to normal.")
        log("recovered", key)
        s["bad"] = False


def main():
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}

    # 1. heartbeat
    try:
        beat = json.loads((OUT / "heartbeat").read_text())
        age = (time.time() - beat["at"]) / 60
    except (OSError, ValueError, KeyError):
        beat, age = {}, 1e9
    stale = age > STALE_MINUTES
    why = ""
    if stale:
        svc, _ = run(["launchctl", "list", "com.scrapling.livemonitor"])
        dock, _ = run(["docker", "info"], timeout=30)
        net, _ = run(["curl", "-s", "-m", "10", "-o", "/dev/null", "https://www.sportybet.com/"])
        why = ("monitor service not loaded" if svc else "Docker is not running" if dock else
               "no internet / SportyBet unreachable" if net else f"stuck at stage '{beat.get('stage', '?')}'")
    alert(state, "Live monitor", stale,
          f"🚨 Live monitor: no completed check for {age:.0f} minutes ({why}). Nothing is being staked.\n"
          "Restart: Scrapling/sportybet/restart_monitor.sh")

    # 2. SportyBet session
    import risk
    bal = risk.balance_now()
    if bal is None:
        code, out = run(["node", "puppeteer/keepalive.js"], cwd=BOT, timeout=120)
        log("keep-alive:", code, out.strip().splitlines()[-1:] if out.strip() else "")
        bal = risk.balance_now()
    alert(state, "SportyBet login", bal is None,
          "🔑 SportyBet login expired and the keep-alive couldn't renew it. Staking will fail until you run "
          "`npm run login` in claudesportybot on the Mac.")

    STATE.write_text(json.dumps(state))
    log(f"heartbeat {age:.1f} min old, balance {bal}")


if __name__ == "__main__":
    main()
