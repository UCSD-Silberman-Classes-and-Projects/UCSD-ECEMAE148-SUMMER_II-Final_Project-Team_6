#!/bin/bash
# Drive loop pinned to cores 2-3 with raised priority, so steering timing is protected.
cd ~/gpscar
exec taskset -c 2,3 nice -n -5 python manage.py drive "$@"
