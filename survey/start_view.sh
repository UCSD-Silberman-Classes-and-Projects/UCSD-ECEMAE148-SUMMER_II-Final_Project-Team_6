#!/bin/bash
# ---- Live survey view in a browser (Claude, 2026-08-31) --------------------
# Serves the frames record_survey.py already wrote to disk, plus live RTK fix.
# Does NOT open the OAK-D, so it cannot fight the recorder for the camera.
#   ~/start_view.sh          -> http://ucsdrobocar-148-06.local:8080
PORT="${1:-8080}"
pkill -f "survey_view[.]py" 2>/dev/null
setsid nohup ~/env/bin/python -u ~/survey_view.py "$PORT" \
    > /tmp/view.log 2>&1 </dev/null &
if curl -s --retry 12 --retry-delay 1 --retry-connrefused --max-time 20 \
        -o /dev/null http://localhost:"$PORT"/stat; then
    IP=$(hostname -I | awk '{print $1}')
    echo "  live view UP"
    echo "    http://$(hostname).local:$PORT"
    echo "    http://$IP:$PORT"
else
    echo "  VIEW FAILED:"; tail -10 /tmp/view.log
fi
