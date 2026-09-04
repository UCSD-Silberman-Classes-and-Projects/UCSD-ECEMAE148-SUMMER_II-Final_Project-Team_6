#!/bin/bash
# ---- Bring up the survey rig (Claude, 2026-08-31) ---------------------------
# Starts the two background pieces that must run BEFORE you drive:
#   1. RTK correction runner  -> turns a 2-5 m fix into a 2 cm fix
#   2. Camera frame recorder  -> saves timestamped JPEGs, no AI
# You then drive with:  ~/drive_survey.sh
#
# PORTS (learned the hard way 8/31): the GPS is ONE dual-port USB chip.
#   if00 = FusionEngine control port -> the runner MUST own this one
#   if01 = NMEA output port          -> DonkeyCar reads this one (myconfig L737)
# Using by-id names so a reboot cannot swap ttyUSB0/ttyUSB1 under us.
# RTK corrections come from Point One Navigation (Polaris). The
# credentials are per-receiver, so they are NOT in this repo - put them
# in ~/.survey_keys (chmod 600) and source it:
#   export P1_DEVICE_ID=your-device-id
#   export P1_POLARIS_KEY=your-polaris-key
[ -f "$HOME/.survey_keys" ] && . "$HOME/.survey_keys"
: "${P1_DEVICE_ID:?set P1_DEVICE_ID (see README)}"
: "${P1_POLARIS_KEY:?set P1_POLARIS_KEY (see README)}"

CTRL=/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_0129F02A-if00-port0
FPS="${1:-4}"

echo "=== 1/2  RTK corrections ==="
# Do NOT restart a healthy runner: every start issues a hot-start reset and the
# receiver needs minutes to climb back to RTKFixed. Reuse an existing lock.
if pgrep -f "bin/runner[.]py" >/dev/null; then
    # Reuse whenever corrections are actually flowing. Fix quality tracks SKY
    # VIEW, not runner health, so restarting a working runner only costs you a
    # hot-start reset and minutes of re-convergence.
    CORR=$(grep -oE "corrections=[0-9]+ B" /tmp/p1.log 2>/dev/null | tail -1 | grep -oE "[0-9]+")
    CUR=$(grep -oE "Type=[A-Za-z]+ \([0-9]\)" /tmp/p1.log 2>/dev/null | tail -1)
    if [ -n "$CORR" ] && [ "$CORR" -gt 0 ]; then
        echo "  runner already UP, corrections flowing (${CORR} B), fix ${CUR:-unknown}"
        echo "  leaving it alone - a restart would reset the receiver"
        SKIP_RTK=1
    fi
fi
if [ -z "$SKIP_RTK" ]; then
pkill -f "bin/runner[.]py" 2>/dev/null; sleep 2
cd ~/quectel/p1_runner || exit 1
rm -f /tmp/p1.log
setsid nohup ~/env/bin/python bin/runner.py \
    --device-id "$P1_DEVICE_ID" --polaris "$P1_POLARIS_KEY" --device-port "$CTRL" \
    > /tmp/p1.log 2>&1 </dev/null &
echo -n "  waiting for RTK lock "
for i in $(seq 1 45); do
    sleep 2; echo -n "."
    FIX=$(grep -oE "Type=[A-Za-z]+" /tmp/p1.log | tail -1 | cut -d= -f2)
    [ "$FIX" = "RTKFixed" ] && break
done
echo
FIX=$(grep -oE "Type=[A-Za-z]+ \([0-9]\)" /tmp/p1.log | tail -1)
case "$FIX" in
  *RTKFixed*)  echo "  RTK FIXED  - centimetre accuracy. Best case." ;;
  *RTKFloat*)  echo "  RTK FLOAT  - decimetre accuracy. Usable; may sharpen to Fixed." ;;
  *)           echo "  WARNING: no RTK lock yet ($FIX). Check sky view + internet."
               tail -3 /tmp/p1.log ;;
esac
fi

echo
# The camera is NOT started here any more. It is started by drive_survey.sh
# once GPS logging is confirmed live -- otherwise you record footage that can
# never be placed on a map (that is what happened on 8/31).
# --camera forces it on anyway, for bench tests with no driving.
if [ "$2" != "--camera" ] && [ "$1" != "--camera" ]; then
    echo "=== 2/2  camera ==="
    echo "  NOT started yet - drive_survey.sh starts it when GPS logging begins."
    echo "  (bench test with no GPS?  ~/start_survey.sh $FPS --camera)"
    echo
    echo "=== ready ==="
    echo "  Now run:  ~/drive_survey.sh     (moves the car AND starts recording)"
    echo "  Status :  ~/survey_status.sh"
    echo "  Stop   :  ~/stop_survey.sh"
    exit 0
fi

echo "=== 2/2  camera recorder (forced: --camera) ==="
pkill -f "record_survey[.]py" 2>/dev/null; sleep 1
cd ~/oakd_project || exit 1
rm -f /tmp/record.log
setsid nohup ~/obj-detection-env/bin/python record_survey.py --fps "$FPS" \
    > /tmp/record.log 2>&1 </dev/null &
sleep 8
grep -qiE "error|traceback" /tmp/record.log && { echo "  RECORDER FAILED:"; tail -8 /tmp/record.log; } \
    || echo "  recording at ${FPS} fps -> $(ls -td ~/oakd_project/data/frames/*/ 2>/dev/null | head -1)"

echo
echo "=== ready ==="
echo "  Now run:  ~/drive_survey.sh     (this is what moves the car)"
echo "  Status :  ~/survey_status.sh"
echo "  Stop   :  ~/stop_survey.sh"
