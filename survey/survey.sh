#!/bin/bash
# =============================================================================
#  survey.sh  --  ONE COMMAND. Runs the whole tree survey.   (Claude, 2026-09-01)
# =============================================================================
#   ~/survey.sh                 one lap, then stop by itself and write the report
#   ~/survey.sh --laps 2        two laps
#   ~/survey.sh --manual        no auto-stop; runs until you press Ctrl-C
#   ~/survey.sh --no-report     stop after the lap, skip the analysis
#   ~/survey.sh --fps 2         record slower (fewer, less redundant frames)
#   ~/survey.sh --check         preflight only - touches nothing, moves nothing
#   ~/survey.sh --every 4       analyse every 4th frame (0/omitted = auto)
#   ~/survey.sh --live --fps 1  DETECT WHILE DRIVING (2 cores, see below)
#   ~/survey.sh --live --conf 0.3   raise/lower the detection floor (default 0.25)
#   ~/survey.sh --live --auto       start driving from here, no controller
#
# LIVE MODE: inference runs at ~0.6 fps on two cores, so recording faster
# than that means it finishes after the lap rather than with it. Two cores
# is not negotiable: four browned the Pi out on 2026-09-02.
#
#  It does, in order:
#    1. preflight   camera / joystick / GPS ports / disk
#    2. RTK         starts corrections, waits for a fix
#    3. drive       starts manage.py so the GPS logger begins writing
#    4. record      camera starts ONLY once positions are being logged
#    5. watch       detects the completed lap from the GPS track
#    6. stop        shuts everything down cleanly
#    7. report      runs the analysis on 2 cores (safe with the camera plugged in)
#
#  Ctrl-C at any point stops everything cleanly - it will not leave the
#  recorder or the drive loop running in the background.
# =============================================================================
set -u

LAPS=1; FPS=4; TIMEOUT=1200; AUTOSTOP=1; REPORT=1; RADIUS=3.0; CHECKONLY=0
# detection floor for --live. 0.25 chosen from the on-HAT val measurement.
CONF=0.25
# --auto: put the car in Full Auto from here instead of the controller.
# The saved path is ALREADY loaded automatically at drive-loop startup
# (manage.py: `if os.path.exists(cfg.PATH_FILENAME): load_path()`), so the
# only thing the gamepad was still needed for is the drive-mode switch.
AUTO=0
DK_WEB="ws://127.0.0.1:8887/wsDrive"   # see set_drive_mode()
# --live: detect DURING the lap instead of afterwards
LIVE=0
# 0 = pick automatically to land near 200 analysed frames
EVERY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --laps)      LAPS="$2"; shift 2 ;;
        --fps)       FPS="$2"; shift 2 ;;
        --timeout)   TIMEOUT="$2"; shift 2 ;;
        --radius)    RADIUS="$2"; shift 2 ;;
        --conf)      CONF="$2"; shift 2 ;;
        --auto)      AUTO=1; shift ;;
        --every)     EVERY="$2"; shift 2 ;;
        --manual)    AUTOSTOP=0; shift ;;
        --check)     CHECKONLY=1; shift ;;
        --live)      LIVE=1; shift ;;
        --no-report) REPORT=0; shift ;;
        -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "unknown option: $1"; exit 1 ;;
    esac
done

# Ask the drive loop for a mode. Returns 0 only if it actually took.
#
# MUST go over the websocket, NOT `POST /drive`. The POST handler sets only
# application.mode; the vehicle loop then calls run_threaded(mode=<user/mode>)
# which reverts it to whatever the joystick just emitted. Only /wsDrive also
# sets mode_latch, which is what survives the next loop. A POST returns 200 and
# does nothing - that is exactly why the car sat still on 2026-09-03.
set_drive_mode() {
    ~/env/bin/python ~/set_drive_mode.py "$1" >/dev/null 2>&1
}

STOPPED=0
stop_all() {
    [ "$STOPPED" = "1" ] && return
    STOPPED=1
    echo
    echo "=== stopping ==="
    # Hand control back and command zero throttle BEFORE killing anything.
    # Killing the drive loop outright leaves the ESC holding its last command
    # until it times out; telling the car to stop first is the safer order.
    if set_drive_mode user; then echo "  car returned to manual, throttle 0"; fi
    pkill -f "record_survey[.]py" 2>/dev/null && echo "  recorder stopped"
    pkill -f "manage[.]py drive"  2>/dev/null && echo "  drive loop stopped"
    pkill -f "[a]rm_camera\.sh"   2>/dev/null
    pkill -f "[r]tk_watchdog"     2>/dev/null
    sleep 2
}
on_int() { echo; echo "[!] interrupted - shutting down cleanly"; stop_all; exit 130; }
trap on_int INT TERM

# ---- 1. preflight -----------------------------------------------------------
echo "=== 1/7  preflight ==="
FAIL=0
if lsusb | grep -qiE "movidius|luxonis"; then echo "  camera    OK"
else echo "  camera    MISSING - plug in the OAK-D"; FAIL=1; fi

# The VESC is powered by the TRACTION battery, not the Pi. When the pack is off
# or flat it vanishes from USB and the drive loop dies at stage 3 with an opaque
# SerialException. Catching it here says what is actually wrong.
if [ -e /dev/ttyACM0 ]; then echo "  vesc      OK"
else
    echo "  vesc      MISSING (/dev/ttyACM0) - the motor controller is not on USB."
    echo "            Check the car's main battery switch and its pack voltage;"
    echo "            the VESC runs off the traction battery, not the Pi."
    FAIL=1
fi

if [ -e /dev/input/js0 ]; then echo "  joystick  OK"
else echo "  joystick  MISSING - the F710/PS4 pad is how you load the path"; FAIL=1; fi

CTRL=/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_0129F02A-if00-port0
if [ -e "$CTRL" ]; then echo "  gps ports OK"
else echo "  gps ports MISSING - is the GPS plugged in?"; FAIL=1; fi

FREE=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
if [ "${FREE:-0}" -ge 5 ]; then echo "  disk      OK (${FREE}G free)"
else echo "  disk      LOW (${FREE}G free)"; FAIL=1; fi

# A metre-level fix cannot follow waypoints that are metres apart: the car
# chases the wrong "nearest point" and circles. Warn before the lap, not after.
FIX=$(grep -oE "Type=[A-Za-z]+ \([0-9]\)" /tmp/p1.log 2>/dev/null | tail -1)
case "$FIX" in
  *RTKFixed*)  echo "  rtk       OK (RTK FIXED - centimetre)" ;;
  *RTKFloat*)  echo "  rtk       OK (RTK FLOAT - decimetre)" ;;
  *DGPS*|*Autonomous*|*GPS\ \(1\)*)
               echo "  rtk       WEAK ($FIX) - metre-level, NOT enough for waypoints."
               echo "            Wait for RTK Float or Fixed, or the car will circle." ;;
  *Invalid*)   echo "  rtk       NO FIX ($FIX) - the receiver has no position."
               echo "            survey.sh will start the runner and wait for lock." ;;
  "")          echo "  rtk       not running yet - will start below" ;;
  *)           echo "  rtk       $FIX" ;;
esac

if [ -f ~/gpscar/donkey_path.csv ]; then
    echo "  path      OK ($(wc -l < ~/gpscar/donkey_path.csv) waypoints)"
else echo "  path      MISSING ~/gpscar/donkey_path.csv"; FAIL=1; fi

if [ "$FAIL" = "1" ]; then
    echo
    echo "Preflight failed. Fix the above and run again."
    exit 1
fi
if [ "$CHECKONLY" = "1" ]; then
    echo
    echo "All preflight checks passed. Nothing was started."
    exit 0
fi

# ---- 2. RTK -----------------------------------------------------------------
echo
echo "=== 2/7  RTK corrections ==="
~/start_survey.sh "$FPS" 2>&1 | sed 's/^/  /' | grep -vE "Now run|Status |Stop  |^  === ready|NOT started|bench test|^  === 2/2" || true

# ---- 3. drive loop (detached, so it cannot swallow your terminal) -----------
# Keep RTK alive if the USB hub drops the GPS port mid-lap (see rtk_watchdog.sh).
pkill -f "[r]tk_watchdog" 2>/dev/null
setsid nohup ~/rtk_watchdog.sh > /tmp/rtk_watchdog.log 2>&1 </dev/null &
echo "  rtk watchdog armed"

echo
echo "=== 3/7  drive loop ==="
pkill -f "manage[.]py drive" 2>/dev/null; sleep 1
cd ~/gpscar || exit 1
rm -f /tmp/drive.log
setsid nohup ~/env/bin/python manage.py drive --js > /tmp/drive.log 2>&1 </dev/null &
sleep 6
if pgrep -f "manage[.]py drive" >/dev/null; then
    echo "  drive loop running (log: /tmp/drive.log)"
else
    echo "  DRIVE LOOP FAILED TO START:"; tail -15 /tmp/drive.log; stop_all; exit 1
fi

# ---- 4. camera, armed to the GPS log ---------------------------------------
echo
echo "=== 4/7  camera ==="
setsid nohup ~/arm_camera.sh "$FPS" > /tmp/armcam.log 2>&1 </dev/null &
echo "  armed - starts recording the moment positions are logged"

if [ "$LIVE" = "1" ]; then
    echo
    echo "=== 4b/7  LIVE detection ==="
    rm -f ~/last_report.log
    # WHICH PYTHON: pyhailort is a C extension built against numpy 1.x. Under
    # obj-detection-env (numpy 2.2.6) every infer() fails with
    #   "Memory size of vstream ... does not match the frame count (got 0)"
    # so the HAT silently produced nothing. System python3 has numpy 1.24.2 and
    # works; ~/hatlibs supplies openai to it without touching any shared env.
    # No HAT model -> fall back to obj-detection-env, which owns the CPU path.
    if [ -f ~/oakd_project/models/grove.hef ] && [ -e /dev/hailo0 ]; then
        LIVE_PY="python3"; LIVE_ENV="PYTHONPATH=$HOME/hatlibs"
        echo "  detector: AI HAT (system python3, numpy 1.x)"
    else
        LIVE_PY="$HOME/obj-detection-env/bin/python"; LIVE_ENV=""
        echo "  detector: CPU (obj-detection-env)"
    fi
    setsid nohup env $LIVE_ENV $LIVE_PY -u ~/oakd_project/live_survey.py \
        --radius "$RADIUS" --conf "$CONF" --cores 2 --llm --narrate \
        > ~/last_report.log 2>&1 </dev/null &
    sleep 2
    echo "  detecting as frames arrive, capped at 2 cores"
    echo "  watch it on the dashboard, or: tail -f ~/last_report.log"
fi

# ---- 5. drive it ------------------------------------------------------------
echo
if [ "$AUTO" = "1" ]; then
    echo "=== 5/7  STARTING THE CAR (--auto) ==="
    echo "     the saved path was loaded automatically at startup"
    if set_drive_mode local; then
        echo "     drive mode -> Full Auto. THE CAR IS NOW DRIVING."
        echo "     stop it with: the dashboard Stop button, Ctrl-C, or the"
        echo "     controller (any stick input takes manual control back)"
    else
        echo "     COULD NOT set the drive mode on $DK_WEB"
        echo "     falling back to the controller: press start to cycle to Full Auto"
    fi
else
    echo "=== 5/7  YOUR TURN - on the controller ==="
    echo "     press  X      load the saved path"
    echo "     press  start  cycle User -> Angle -> Full Auto"
    echo "     any stick input instantly takes manual control back"
fi
echo
if [ "$AUTOSTOP" = "0" ]; then
    echo "=== 6/7  running until you press Ctrl-C (--manual) ==="
    while true; do sleep 5; done
else
    echo "=== 6/7  watching for lap completion ==="
    ~/obj-detection-env/bin/python ~/lap_watch.py --laps "$LAPS" --timeout "$TIMEOUT"
    RC=$?
    case "$RC" in
        0) echo "  lap(s) done - shutting down" ;;
        2) echo "  timed out - shutting down anyway" ;;
        *) echo "  watcher exited ($RC) - shutting down" ;;
    esac
fi

# ---- 7. stop + report -------------------------------------------------------
stop_all
RUN=$(basename "$(ls -td ~/oakd_project/data/frames/*/ 2>/dev/null | head -1)")
N=$(ls ~/oakd_project/data/frames/"$RUN" 2>/dev/null | wc -l)
echo
echo "=== 7/7  run complete ==="
echo "  run    : $RUN"
echo "  frames : $N"

if [ "$LIVE" = "1" ]; then
    echo
    echo "=== live detection is finishing off ==="
    echo "  it keeps going until it has caught up, then writes the report."
    echo "  watch    :  ~/report_status.sh   (or the dashboard)"
    echo "  dashboard:  on your Mac, ~/survey_dashboard.sh"
elif [ "$REPORT" = "1" ] && [ -n "$RUN" ] && [ "$N" -gt 0 ]; then
    if [ "$EVERY" -le 0 ] 2>/dev/null; then
        # aim at roughly 200 analysed frames: enough coverage, ~8x less compute
        EVERY=$(( N / 200 )); [ "$EVERY" -lt 1 ] && EVERY=1
    fi
    echo
    echo "=== analysis ==="
    echo "  every $EVERY th frame, 2 cores (camera can stay plugged in)"
    ~/report_survey.sh "$RUN" "$RADIUS" "$EVERY" 2
    echo
    echo "  watch it :  ~/report_status.sh"
    echo "  dashboard:  on your Mac, run  ~/survey_dashboard.sh"
else
    echo "  report skipped. Run it later with:"
    echo "    ~/report_survey.sh $RUN $RADIUS 8 2"
fi
