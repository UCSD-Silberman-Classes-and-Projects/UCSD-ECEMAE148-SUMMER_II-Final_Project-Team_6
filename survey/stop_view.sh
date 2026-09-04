#!/bin/bash
pkill -f "survey_view[.]py" && echo "live view stopped" || echo "live view was not running"
