#!/bin/zsh
# Restart the live monitor safely: never while a stake is being placed (out/live/STAKING exists).
LOCK=${0:A:h}/out/live/STAKING
for i in $(seq 1 120); do
  [[ -e $LOCK ]] || break
  (( i == 1 )) && echo "a stake is being placed ($(cat $LOCK)); waiting..."
  sleep 5
done
[[ -e $LOCK ]] && { echo "still staking after 10 minutes; not restarting"; exit 1; }
launchctl kickstart -k gui/$(id -u)/com.scrapling.livemonitor && echo "restarted"
