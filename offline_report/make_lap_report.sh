#!/bin/bash
# Build a single self-contained HTML report for a lap and drop it on the Desktop.
#
#   ~/make_lap_report.sh                 newest run
#   ~/make_lap_report.sh 20260903_165910 a specific run
#   ~/make_lap_report.sh 20260903_165910 40   lower JPEG quality = smaller file
#
# Everything is embedded - frames, both reports, narration, map. The file opens
# with no internet and no server: double-click it, or send it to a teammate.
set -u
HOST=robocar
RUN="${1:-}"
Q="${2:-50}"

ssh -o ConnectTimeout=8 "$HOST" true 2>/dev/null || { echo "cannot reach the Pi"; exit 1; }

if [ -z "$RUN" ]; then
    RUN=$(ssh "$HOST" 'basename "$(ls -td ~/oakd_project/data/frames/*/ | head -1)"')
    echo "  newest run: $RUN"
fi

OUT="$HOME/Desktop/Grove_lap_${RUN}.html"
echo "  building on the Pi (the frames never cross the wire) ..."
ssh "$HOST" "cd ~/offline_report && python3 make_offline.py '$RUN' /tmp/grove_lap.html $Q" \
    | sed 's/^/    /' || { echo "  build failed"; exit 1; }

scp -q "$HOST":/tmp/grove_lap.html "$OUT" || { echo "  copy failed"; exit 1; }
ssh "$HOST" 'rm -f /tmp/grove_lap.html'

# A stray http:// or https:// would mean something is NOT embedded and the file
# would look broken offline - exactly what this is supposed to avoid.
if grep -qoE 'https?://' "$OUT"; then
    echo "  WARNING: external references found - it may not work offline:"
    grep -oE 'https?://[^"'"'"' )]+' "$OUT" | sort -u | head
else
    echo "  verified: no external references"
fi
printf "  %s  (%.1f MB)\n" "$OUT" "$(echo "scale=1; $(stat -f%z "$OUT")/1048576" | bc)"
echo "  double-click it, or send the single file to a teammate."
