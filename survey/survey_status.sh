#!/bin/bash
# ---- What is running right now (Claude, 2026-08-31) ------------------------
echo "=== RTK ==="
if pgrep -f "bin/runner[.]py" >/dev/null; then
    echo "  runner: UP    fix: $(grep -oE "Type=[A-Za-z]+ \([0-9]\)" /tmp/p1.log 2>/dev/null | tail -1)"
    echo "  $(grep -oE "corrections=[0-9]+ B" /tmp/p1.log 2>/dev/null | tail -1) received"
else
    echo "  runner: DOWN  -> fix will be plain GPS, 2-5 m"
fi
echo "=== camera ==="
if pgrep -f "record_survey[.]py" >/dev/null; then
    D=$(ls -td ~/oakd_project/data/frames/*/ 2>/dev/null | head -1)
    echo "  recorder: UP   $(ls "$D" 2>/dev/null | wc -l) frames in $(basename "$D")"
else
    echo "  recorder: DOWN"
fi
echo "=== drive loop ==="
pgrep -f "manage[.]py drive" >/dev/null && echo "  manage.py: UP" || echo "  manage.py: DOWN"
echo "=== gps log ==="
G=$(ls -t ~/gpscar/logs/gps_*.csv 2>/dev/null | head -1)
if [ -n "$G" ]; then
    echo "  $(basename "$G"): $(( $(wc -l < "$G") - 1 )) samples"
    echo "  fix values seen: $(tail -n +2 "$G" | cut -d, -f4 | sort -u | tr "\n" " ")"
else
    echo "  (no GPS log yet)"
fi
echo "=== health ==="
echo "  temp $(vcgencmd measure_temp | cut -d= -f2) | idle $(vmstat 1 2 | tail -1 | awk '{print $15}')% | free $(df -h / | awk 'NR==2{print $4}')"
