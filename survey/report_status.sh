#!/bin/bash
# ---- Is the analysis still going? Did it finish? (Claude, 2026-08-31) -------
LOG=~/last_report.log
echo -n "uptime: "; awk '{printf "%d min %d s\n", $1/60, $1%60}' /proc/uptime
if pgrep -f "[a]nalyze_survey\.py" >/dev/null; then
    echo "analysis: RUNNING (pid $(pgrep -f '[a]nalyze_survey\.py' | head -1))"
else
    echo "analysis: not running"
fi
echo "progress: $(grep -oE '[0-9]+/[0-9]+ frames[^|]*\|[^|]*' "$LOG" 2>/dev/null | tail -1)"
echo "--- last 6 lines of $LOG ---"
tail -6 "$LOG" 2>/dev/null || echo "(no log)"
echo "--- reports on disk ---"
ls -lt ~/oakd_project/reports/ 2>/dev/null | head -5
