"""
survey_gps_logger.py  --  MAE 148 Team 6

A DonkeyCar part that logs the car's position continuously, in EVERY mode.

Why this exists:
    manage.py already computes pos/x, pos/y, but the only NMEA logger runs
    with inputs=['recording', ...], so it records in USER mode only. During
    an autonomous lap nothing is written, which is why data/logs was empty
    and why no detection could ever be tagged with a location.

It also parses the GGA fix quality so a lap tells you whether RTK was
actually live:
    1 = plain GPS (~2-5 m error, too coarse to place a tree)
    2 = DGPS
    4 = RTK FIXED   <-- what you want, centimetre level
    5 = RTK float

SAFETY: run() can never raise. If anything goes wrong it counts the error
and returns, because a logging bug must not be able to stop the car.
"""

import os
import re
import time

# "$GNGGA,hhmmss,lat,N,lon,E,<quality>,..."  - field 6 is fix quality
_GGA = re.compile(r"\$G[NP]GGA(?:,[^,]*){5},(\d)")

_QUALITY = {
    0: "no-fix", 1: "gps", 2: "dgps", 3: "pps",
    4: "RTK-FIXED", 5: "rtk-float", 6: "estimated",
}


class SurveyGpsLogger:
    """Appends time,x,y,fix to a CSV every drive loop."""

    def __init__(self, path, min_interval=0.1, debug=False):
        self.path = path
        self.min_interval = min_interval      # throttle: 10 Hz is plenty
        self.debug = debug
        self.errors = 0
        self.rows = 0
        self.last_write = 0.0
        self.last_fix = ""
        self.fix_counts = {}
        self._fh = None

        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            self._fh = open(path, "w", buffering=1)   # line buffered
            self._fh.write("unix_time,pos_x,pos_y,fix_quality,fix_name\n")
            print(f"[survey_gps] logging positions -> {path}")
        except Exception as e:
            print(f"[survey_gps] could not open log: {e}")
            self._fh = None

    def _fix_from_nmea(self, nmea):
        """Best-effort GGA fix quality. Accepts whatever shape nmea is."""
        if nmea is None:
            return None
        try:
            m = _GGA.search(str(nmea))
            return int(m.group(1)) if m else None
        except Exception:
            return None

    def run(self, pos_x=None, pos_y=None, nmea=None):
        # Must never raise - this runs inside the drive loop.
        try:
            if self._fh is None or pos_x is None or pos_y is None:
                return

            now = time.time()
            if now - self.last_write < self.min_interval:
                return
            self.last_write = now

            fix = self._fix_from_nmea(nmea)
            name = _QUALITY.get(fix, "") if fix is not None else ""
            if name:
                self.fix_counts[name] = self.fix_counts.get(name, 0) + 1
                if name != self.last_fix:
                    print(f"[survey_gps] fix quality now: {name}")
                    self.last_fix = name

            self._fh.write(f"{now:.3f},{pos_x},{pos_y},"
                           f"{fix if fix is not None else ''},{name}\n")
            self.rows += 1
        except Exception:
            self.errors += 1

    def shutdown(self):
        try:
            if self._fh:
                self._fh.flush()
                self._fh.close()
            print(f"[survey_gps] wrote {self.rows} positions "
                  f"({self.errors} errors) -> {self.path}")
            if self.fix_counts:
                print(f"[survey_gps] fix quality seen: {self.fix_counts}")
                if "RTK-FIXED" not in self.fix_counts:
                    print("[survey_gps] NOTE: never reached RTK-FIXED. "
                          "Positions are metre-level, not centimetre-level.")
        except Exception:
            pass
