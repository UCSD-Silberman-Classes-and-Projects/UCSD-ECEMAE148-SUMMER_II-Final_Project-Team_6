#!/bin/bash
# ---- Start recording only once GPS is actually logging (Claude, 2026-09-01) --
# WHY THIS EXISTS: the position logger is a DonkeyCar part living inside
# manage.py (~/gpscar/manage.py:176), so it writes ONLY while drive_survey.sh
# is running. On 8/31 the camera was started first and ~11,000 frames across
# four runs were recorded with no positions at all -- good footage that could
# never be mapped or de-duplicated. Tying the recorder to the GPS log means
# every frame recorded can be placed.
FPS="${1:-4}"
MAXWAIT="${2:-150}"
LOGDIR=~/gpscar/logs

echo "[arm] waiting for GPS logging to start (up to ${MAXWAIT}s) ..."
ok=""
for i in $(seq 1 "$MAXWAIT"); do
    newest=$(ls -t "$LOGDIR"/gps_*.csv 2>/dev/null | head -1)
    if [ -n "$newest" ]; then
        age=$(( $(date +%s) - $(stat -c %Y "$newest") ))
        rows=$(wc -l < "$newest" 2>/dev/null || echo 0)
        # fresh AND growing: a stale log from a previous lap must not count
        if [ "$age" -le 5 ] && [ "$rows" -gt 3 ]; then
            ok=1
            echo "[arm] GPS live: $(basename "$newest") (${rows} rows)"
            break
        fi
    fi
    sleep 1
done

if [ -z "$ok" ]; then
    echo "[arm] WARNING: GPS never started logging after ${MAXWAIT}s."
    echo "[arm] Recording anyway so the drive is not lost, but these frames"
    echo "[arm] will have NO positions and cannot be mapped."
fi

pkill -f "record_survey[.]py" 2>/dev/null; sleep 1
cd ~/oakd_project || exit 1
rm -f /tmp/record.log
setsid nohup ~/obj-detection-env/bin/python record_survey.py --fps "$FPS" \
    > /tmp/record.log 2>&1 </dev/null &
sleep 8
if grep -qiE "error|traceback" /tmp/record.log; then
    echo "[arm] RECORDER FAILED:"; tail -8 /tmp/record.log
else
    echo "[arm] recording at ${FPS} fps -> $(ls -td ~/oakd_project/data/frames/*/ 2>/dev/null | head -1)"
fi
