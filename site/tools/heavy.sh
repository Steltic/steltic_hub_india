#!/bin/bash
# heavy.sh <cmd...>  -- run a heavy analysis under one of the box-wide slots (a small box: 2 CPU cores, 7 GB RAM).
# Waits for a free slot; the slot is held until the command (and its children) exit.
# HEAVY_LOCKS (default /home/claude/locks) holds the lock files and heavy.log; HEAVY_SLOTS (default 2) the slots.
LOCKS=${HEAVY_LOCKS:-/home/claude/locks}
SLOTS=${HEAVY_SLOTS:-2}
mkdir -p "$LOCKS"
while true; do
  for s in $(seq 1 "$SLOTS"); do
    exec 9>"$LOCKS/slot$s"
    if flock -n 9; then
      echo "[heavy] slot$s $(date +%FT%T) start: $*" >> "$LOCKS/heavy.log"
      "$@"; rc=$?
      echo "[heavy] slot$s $(date +%FT%T) end rc=$rc: $*" >> "$LOCKS/heavy.log"
      exit $rc
    fi
    exec 9>&-
  done
  sleep 15
done
