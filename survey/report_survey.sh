#!/bin/bash
# ---- Analyse a finished run and write the LLM report (Claude, 2026-08-31) ---
#   ~/report_survey.sh                  newest run, every 8th frame
#   ~/report_survey.sh <run>            "
#   ~/report_survey.sh <run> 5          merge radius 5 m
#   ~/report_survey.sh <run> 3 1        EVERY frame (slow: ~1 fps of compute)
#   ~/report_survey.sh <run> 3 16 2     2 cores only -- safe with camera plugged in
#
# WHY EVERY 8 BY DEFAULT: the camera records at 4 fps and a lot of the run is
# the car sitting still. Consecutive frames are near-identical and the merge
# radius throws the duplicates away anyway. 8 -> 0.5 fps, ~8x less compute.
#
# DETACHED: survives your SSH dropping. Does NOT survive the Pi losing power.
# Log lives in ~ (not /tmp) because /tmp is wiped on every reboot.
LOG=~/last_report.log
[ -f ~/.survey_keys ] && . ~/.survey_keys
RUN="${1:-$(basename "$(ls -td ~/oakd_project/data/frames/*/ 2>/dev/null | head -1)")}"
RADIUS="${2:-3.0}"
EVERY="${3:-8}"
# 4th arg: how many CPU cores inference may use. 0 = all 4 (fastest, highest
# peak current). On a 3 A supply the Pi 5 browns out when 4-core inference and
# the OAK-D draw peak together -- 2 cores keeps the camera plugged in safely.
CORES="${4:-0}"
[ -z "$RUN" ] && { echo "No recorded runs found."; exit 1; }

if pgrep -f "[a]nalyze_survey\.py" >/dev/null; then
    echo "An analysis is ALREADY running:"; pgrep -af "[a]nalyze_survey\.py"
    echo "  watch:  tail -f $LOG"
    echo "  kill :  pkill -f '[a]nalyze_survey\\.py'"
    exit 1
fi

NROWS=$(( $(wc -l < ~/oakd_project/data/logs/frames_"$RUN".csv 2>/dev/null || echo 1) - 1 ))
NUSED=$(( NROWS / EVERY ))
echo "Analysing run: $RUN"
echo "  frames indexed : $NROWS"
echo "  every          : $EVERY  -> ~$NUSED frames analysed"
echo "  merge radius   : ${RADIUS} m"
echo "  est. runtime   : ~$(( NUSED / 60 + 1 )) min at ~1 fps"

if [ "$CORES" -gt 0 ] 2>/dev/null; then
    NCPU=$(nproc)
    [ "$CORES" -gt "$NCPU" ] && CORES=$NCPU
    PIN="taskset -c 0-$((CORES-1))"
    export OMP_NUM_THREADS=$CORES OPENBLAS_NUM_THREADS=$CORES MKL_NUM_THREADS=$CORES \
           NUMEXPR_NUM_THREADS=$CORES ORT_NUM_THREADS=$CORES TORCH_NUM_THREADS=$CORES
    echo "  cpu limit      : $CORES of $NCPU cores (low-power mode)"
else
    PIN=""
    echo "  cpu limit      : none (all cores -- unplug the OAK-D on a 3 A supply)"
fi

rm -f "$LOG"
setsid nohup $PIN ~/obj-detection-env/bin/python -u ~/oakd_project/analyze_survey.py \
     --run "$RUN" --radius "$RADIUS" --every "$EVERY" --llm > "$LOG" 2>&1 </dev/null &

sleep 3 2>/dev/null || true
if pgrep -f "[a]nalyze_survey\.py" >/dev/null; then
    echo "  started OK (pid $(pgrep -f '[a]nalyze_survey\.py' | head -1))"
    echo "  watch :  tail -f $LOG"
    echo "  check :  ~/report_status.sh"
else
    echo "  FAILED to start:"; tail -20 "$LOG"
fi
