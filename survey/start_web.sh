#!/bin/bash
# ---- Survey results dashboard (Claude, 2026-09-01) --------------------------
# The dashboard normally runs as a systemd service (survey-web.service) and
# starts itself at boot. This script just restarts it, and prints the addresses
# that are actually reachable from another machine.
#   ~/start_web.sh          restart the service
#   ~/start_web.sh 8090     same (port is fixed in the unit)
PORT=8090
if systemctl list-unit-files 2>/dev/null | grep -q "^survey-web.service"; then
    sudo systemctl restart survey-web.service
else
    # fallback if the service was never installed
    pkill -f "[s]urvey_web\.py" 2>/dev/null
    setsid nohup ~/env/bin/python -u ~/survey_web.py "$PORT" \
        > ~/survey_web.log 2>&1 </dev/null &
fi

if curl -s --retry 15 --retry-delay 1 --retry-connrefused --max-time 25 \
        -o /dev/null http://localhost:"$PORT"/api/runs; then
    echo "  dashboard UP"
    echo "    http://$(hostname).local:$PORT      <- give teammates THIS one"
    # Only real LAN interfaces. docker0 (172.17.x) and any point-to-point link
    # are useless to another machine and only cause confusion.
    for ip in $(hostname -I); do
        case "$ip" in
            172.1[6-9].*|172.2[0-9].*|172.3[01].*) continue ;;  # docker bridge
            169.254.*) continue ;;                               # link-local
        esac
        echo "    http://$ip:$PORT"
    done
else
    echo "  FAILED:"; tail -15 ~/survey_web.log
fi
