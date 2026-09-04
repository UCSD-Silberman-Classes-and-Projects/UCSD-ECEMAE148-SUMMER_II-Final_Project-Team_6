#!/bin/bash
# Run in a 2nd terminal ~15s AFTER launching. Confirms the VESC is actually driving.
docker exec robocar_team6 bash -lc "source /opt/ros/jazzy/setup.bash && export ROS_DOMAIN_ID=6 && ros2 topic info /cmd_vel" | grep -E "Subscription" | \
while read line; do
  n=$(echo "$line" | grep -o "[0-9]*")
  if [ "$n" = "1" ]; then echo "OK - VESC is subscribed, the car will move"
  else echo "*** BAD: Subscription count=$n - VESC NODE IS DEAD, Ctrl-C and relaunch ***"; fi
done
