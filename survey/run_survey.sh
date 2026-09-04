#!/bin/bash
# ---- Survey launcher (Claude, 2026-08-31) ------------------------------------
#   ~/run_survey.sh             full speed, 4 cores, 1.16 fps   (car PARKED)
#   ~/run_survey.sh --driving   pinned 0-1, 0.61 fps            (car DRIVING)
#
# Why --driving exists: unpinned, inference took 352% CPU (all 4 cores), 0% idle,
# 79 C. The drive loop was starved and the car left the route. Pinning to cores
# 0-1 guarantees cores 2-3 stay free for steering.
#
# OFFLINE_MODE + MODEL_CACHE_DIR: the default cache is /tmp/cache, which this Pi
# ERASES ON EVERY BOOT. Weights now live in ~/.inference_cache, so the app starts
# with no internet at the track.
export MODEL_CACHE_DIR="$HOME/.inference_cache"
export OFFLINE_MODE=True
# The Roboflow key is per-account and is NOT in this repo. Put it in
# ~/.survey_keys (chmod 600) and source it:
#   export ROBOFLOW_API_KEY=your-key
[ -f "$HOME/.survey_keys" ] && . "$HOME/.survey_keys"

PIN=""; TH=4
if [ "$1" = "--driving" ]; then PIN="taskset -c 0,1"; TH=2; shift
  echo "[run_survey] DRIVING mode: cores 0-1 only, cores 2-3 reserved for steering"
else
  echo "[run_survey] FULL-SPEED mode: all 4 cores. Do NOT drive the car like this."
fi
export OMP_NUM_THREADS=$TH OPENBLAS_NUM_THREADS=$TH MKL_NUM_THREADS=$TH
cd ~/oakd_project
exec $PIN nice -n 10 ~/obj-detection-env/bin/python "${1:-test_rfdetr_web2.py}"
