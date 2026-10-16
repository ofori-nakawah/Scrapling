# scrapling-custom

Scrapling (D4Vinci/Scrapling) plus our own work. Three remotes:

| Remote | Repo | Role |
|---|---|---|
| `origin` | ofori-nakawah/scrapling-custom (private) | **our code**: push here |
| `fork` | ofori-nakawah/Scrapling (public fork) | a clean mirror of upstream; never commit our work to it |
| `upstream` | D4Vinci/Scrapling | the original, read-only |

## What's ours

- `sportybet/`: SportyBet scrapers (today's games, goal stats, predictions), the live monitor and auto-stake routine
  (`live_monitor.py`, launchd `com.scrapling.livemonitor`), booking helpers.
- `pexels/`: the Pexels crawler and HTTP API (`server.py`, container `pexels-api` on :8090).
- `Dockerfile.stealth`: `scrapling:local` plus Patchright's Chromium for `StealthyFetcher`.
- `.gitignore` additions: scraped data, tickets, downloads and the gismo token stay local.

## Taking upstream updates (only when we want them)

1. Bring the fork up to date: press **Sync fork** on GitHub, or `gh repo sync ofori-nakawah/Scrapling`.
2. See what changed before taking it:
   ```bash
   git fetch fork
   git log --oneline main..fork/main        # new upstream commits
   git diff main...fork/main --stat         # files they touch
   ```
3. Take them on a branch, test, then merge:
   ```bash
   git switch -c upstream-sync
   git merge fork/main                      # conflicts are usually only in .gitignore
   docker build -t scrapling:local . && docker build -f Dockerfile.stealth -t scrapling:stealth .
   # run the sportybet / pexels scripts once, then:
   git switch main && git merge upstream-sync && git push origin main
   ```
   The live monitor and the Pexels API use the `scrapling:local` / `scrapling:stealth` images, so rebuild both
   and restart them (`launchctl kickstart -k gui/$(id -u)/com.scrapling.livemonitor`, `docker restart pexels-api`).

## The live monitor's worker container

`live_monitor.py` runs the scripts inside a long-running container, `scrapling-worker` (about 1 s a call
instead of about 6 s for a fresh `docker run ... uv run`). It is created on demand from `scrapling:local`.
After rebuilding that image, remove it so the next pass starts it from the new image:
`docker rm -f scrapling-worker`.

## The warm betslip

`sportybet/betslip_daemon.py` keeps one signed-in Scrapling browser open in `scrapling-worker` and places
stakes through SportyBet's real betslip (a port of claudesportybot's `stage-bet.js`, with all its checks):
about 10 s a stake instead of 20-30 s. The worker mounts `claudesportybot/.chrome-profile` read-only for
the saved session, which pulld's keep-alive renews hourly. `betslip.py CODE --stake N --to-confirm` tests
everything up to SportyBet's confirm panel without confirming. If the daemon is down, the monitor falls
back to `stage-bet.js`.

## The rules

Every rule the live monitor uses is in `sportybet/rules.json`: stake, windows, goals to lose, pre-match bar,
live check, price cap, limits, Telegram messages. It is read at the start of every check, so an edit applies
within a minute with no restart. A broken file keeps the last good rules and sends one Telegram alert.
