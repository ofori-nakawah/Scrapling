#!/bin/zsh
# Watch live football for ONE 1.01 pick in the 80th-89th minute, book it and stake it once.
# Stops after the first placement attempt that clicked anything, or at END_UTC.
STAKE=${STAKE:-5}
END_UTC=${END_UTC:-$(date -u -v+5H +%s)}
HERE=${0:A:h}
BOT=/Users/apple/Projects/claudesportybot
LOG=$HERE/out/watch_101.log
mkdir -p $HERE/out
log() { echo "$(date -u +%H:%M:%S) $*" | tee -a $LOG; }
log "watching: 1.01 picks, 80-89', stake GHS $STAKE, until $(date -u -r $END_UTC +%H:%M) UTC"
while (( $(date -u +%s) < END_UTC )); do
  hit=$(docker run --rm -v "$HERE:/work" --entrypoint uv scrapling:local run python /work/find_101.py 2>>$LOG | grep '^{' | tail -1)
  if [[ -n $hit ]]; then
    code=$(print -r -- $hit | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
    log "FOUND $hit"
    out=$(cd $BOT && npm run -s stage -- $code --stake $STAKE --place --headless 2>&1); rc=$?
    print -r -- "$out" >> $LOG
    log "stage exit $rc"
    if (( rc == 0 )); then log "PLACED $code"; print -r -- "RESULT placed $hit"; exit 0; fi
    if [[ $out == *"Nothing was placed"* ]]; then log "not placed, nothing clicked; looking again"; sleep 20; continue; fi
    log "STOPPED: result needs checking in Bet History (exit $rc); not retrying"
    print -r -- "RESULT check $hit"; exit 3
  fi
  sleep 45
done
log "no 1.01 pick in the 80s before the deadline"; print -r -- "RESULT none"; exit 4
