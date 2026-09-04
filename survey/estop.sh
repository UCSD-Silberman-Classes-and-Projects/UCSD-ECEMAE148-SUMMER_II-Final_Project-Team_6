#!/bin/bash
# Emergency stop: kill everything driving the car.
docker exec robocar_team6 bash -lc "ps -eo pid,args | grep -E \"[a]ll_nodes|[l]ane_guidance|[l]ane_detection|[v]esc_twist|[m]ulti_cam/cams|[c]alibration_node\" | awk \"{print \\\$1}\" | xargs -r kill -9"
echo "ESTOP: all driving nodes killed. VESC failsafe will cut the motor."
