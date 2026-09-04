#!/bin/bash
# ---- Drive the saved GPS route (Claude, 2026-08-31) -------------------------
# THIS ONE MOVES THE CAR. Run ~/start_survey.sh first.
# Path: ~/gpscar/donkey_path.csv  (482 waypoints, 108 m closed loop)
# F710: "X" loads the saved path | "start" cycles User -> Angle -> Full Auto
#       any stick input instantly reclaims manual control
cd ~/gpscar || exit 1
if ! pgrep -f "bin/runner[.]py" >/dev/null; then
    echo "WARNING: RTK runner is NOT running - you will drive on a 2-5 m fix."
    echo "         Run ~/start_survey.sh first. Ctrl-C now if that is wrong."
    sleep 5
fi
# NOTE: this manage.py (donkey 5.3.0 gpscar template) accepts ONLY [--js] [--log]
# Do NOT add --myconfig: docopt prints usage and exits 0, which looks like a silent no-op.
# myconfig.py is loaded automatically from the cwd (~/gpscar).
# Arm the camera: it starts recording the instant the GPS logger inside
# manage.py begins writing, so frames and positions always overlap.
# Pass an fps as $1 if you want something other than 4.
FPS="${1:-4}"
setsid nohup ~/arm_camera.sh "$FPS" > /tmp/armcam.log 2>&1 </dev/null &
echo "camera armed - recording starts when GPS logging does."
echo "  watch it:  tail -f /tmp/armcam.log"
echo

exec ~/env/bin/python manage.py drive --js
