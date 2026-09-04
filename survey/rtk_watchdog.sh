#!/bin/bash
# ---- Keep RTK alive across USB re-enumeration -------- (Claude, 2026-09-02) --
# On 2026-09-02 the USB hub dropped the GPS control port mid-lap. /dev/ttyUSB0
# was recreated, but p1_runner still held the OLD file descriptor: it kept
# downloading corrections from the network and silently failed to deliver any
# of them. The receiver decayed RTK-FIXED -> float -> dgps -> gps -> Invalid,
# and the car began chasing waypoints it could no longer locate.
#
# Nothing in the stack noticed, because the runner process was still alive and
# the correction byte counter was still climbing. This watches the thing that
# actually matters -- whether the receiver has a POSITION -- and restarts the
# runner when it does not.
#
#   ~/rtk_watchdog.sh [grace_seconds]      (default 25)
# RTK corrections come from Point One Navigation (Polaris). The
# credentials are per-receiver, so they are NOT in this repo - put them
# in ~/.survey_keys (chmod 600) and source it:
#   export P1_DEVICE_ID=your-device-id
#   export P1_POLARIS_KEY=your-polaris-key
[ -f "$HOME/.survey_keys" ] && . "$HOME/.survey_keys"
: "${P1_DEVICE_ID:?set P1_DEVICE_ID (see README)}"
: "${P1_POLARIS_KEY:?set P1_POLARIS_KEY (see README)}"

CTRL=/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_0129F02A-if00-port0
GRACE="${1:-25}"
LOG=/tmp/p1.log
bad=0

restart_runner() {
    echo "[rtk] restarting the correction runner (port was $( [ -e "$CTRL" ] && echo present || echo MISSING ))"
    live_runners() { ps -eo pid,args | awk '/bin\/runner\.py/ && !/awk/ {print $1}'; }
    PIDS=$(live_runners)
    if [ -n "$PIDS" ]; then
        kill $PIDS 2>/dev/null
        # A WEDGED RUNNER CAN IGNORE SIGTERM. On 2026-09-03 one did exactly
        # that: it outlived the old fixed 3 s grace, this watchdog started a
        # second, and the two then fought over the control port. Every reset
        # request timed out, corrections never flowed, and RTK never locked for
        # the whole session. Wait for the old one to actually die, then force it.
        for _ in 1 2 3 4 5 6; do
            sleep 1
            [ -z "$(live_runners)" ] && break
        done
        if [ -n "$(live_runners)" ]; then
            echo "[rtk] runner ignored SIGTERM - forcing"
            kill -9 $(live_runners) 2>/dev/null
            sleep 2
        fi
    fi
    # Never add a second runner while one still holds the port: two runners is
    # strictly worse than none, because it cannot recover on its own.
    if [ -n "$(live_runners)" ]; then
        echo "[rtk] old runner will not die - refusing to start a duplicate"
        return 1
    fi
    [ -e "$CTRL" ] || { echo "[rtk] control port absent - cannot restart yet"; return 1; }
    cd ~/quectel/p1_runner || return 1
    mv -f "$LOG" "$LOG.prev" 2>/dev/null
    setsid nohup ~/env/bin/python bin/runner.py \
        --device-id "$P1_DEVICE_ID" --polaris "$P1_POLARIS_KEY" --device-port "$CTRL" \
        > "$LOG" 2>&1 </dev/null &
    echo "[rtk] runner restarted; re-convergence takes a minute or two"
    return 0
}

echo "[rtk] watchdog up - restarts the runner if the fix stays invalid ${GRACE}s"
while true; do
    sleep 5
    # last reported fix type; nan/Invalid means the receiver has no solution
    last=$(grep -oE "Type=[A-Za-z]+ \([0-9]\)" "$LOG" 2>/dev/null | tail -1)
    case "$last" in
        *Invalid*|"")
            bad=$((bad + 5))
            if [ "$bad" -ge "$GRACE" ]; then
                echo "[rtk] no position solution for ${bad}s (last: ${last:-none})"
                restart_runner && bad=0 || bad=$((GRACE - 10))
            fi
            ;;
        *)
            [ "$bad" -gt 0 ] && echo "[rtk] fix recovered: $last"
            bad=0
            ;;
    esac
done
