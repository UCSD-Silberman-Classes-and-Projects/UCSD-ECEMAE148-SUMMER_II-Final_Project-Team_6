#!/bin/bash
# ---- Stop everything cleanly (Claude, 2026-08-31) --------------------------
pkill -f "record_survey[.]py" && echo "recorder stopped"  || echo "recorder was not running"
pkill -f "manage[.]py drive"  && echo "drive loop stopped" || echo "drive loop was not running"
pkill -f "bin/runner[.]py"    && echo "RTK runner stopped" || echo "RTK runner was not running"
sleep 2
D=$(ls -td ~/oakd_project/data/frames/*/ 2>/dev/null | head -1)
[ -n "$D" ] && echo "last run: $(basename "$D") with $(ls "$D" | wc -l) frames"
echo "report with: ~/report_survey.sh $(basename "$D" 2>/dev/null)"
