#!/usr/bin/env python3
"""
lap_watch.py  --  MAE 148 Team 6            (Claude, 2026-09-01)

Watches the live GPS log and exits when the car has completed its lap, so the
survey can shut itself down instead of waiting for someone to notice.

HOW COMPLETION IS DETECTED
    The saved route is a 107.9 m closed loop, but the car does not necessarily
    start at waypoint 0, so we do not compare against the saved path at all.
    Instead we anchor on the car's OWN start: the first fix after it has moved
    a couple of metres. A lap is complete when the car has travelled at least
    a set fraction of the loop AND has come back within a few metres of that
    anchor. Requiring BOTH is what stops it from declaring victory while the
    car is still sitting near the start jittering on GPS noise.

EXIT CODES
    0  lap(s) completed
    2  timed out
    3  interrupted
"""
import argparse
import csv
import glob
import math
import os
import sys
import time

GPSLOGS = os.path.expanduser("~/gpscar/logs")
LOOP_M = 107.9          # measured from donkey_path.csv


def newest_log(fresh=None):
    """The GPS log being written RIGHT NOW.

    Freshness is not optional. Logs from previous sessions are still on disk,
    and the newest of those is a COMPLETED lap -- watching one would replay it
    in a fraction of a second and declare the run finished before the car has
    moved. A log only counts if it has been touched in the last `fresh`
    seconds, i.e. something is actively appending to it.
    """
    logs = sorted(glob.glob(os.path.join(GPSLOGS, "gps_*.csv")),
                  key=os.path.getmtime)
    if not logs:
        return None
    p = logs[-1]
    if fresh is not None and (time.time() - os.path.getmtime(p)) > fresh:
        return None
    return p


def read_track(path):
    out = []
    try:
        with open(path) as fh:
            for r in csv.DictReader(fh):
                try:
                    out.append((float(r["unix_time"]), float(r["pos_x"]),
                                float(r["pos_y"]), r.get("fix_name", "")))
                except (TypeError, ValueError, KeyError):
                    continue
    except OSError:
        return []
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--laps", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=1200,
                    help="give up after this many seconds (default 20 min)")
    ap.add_argument("--min-frac", type=float, default=0.75,
                    help="fraction of the loop that must be driven (0.75)")
    ap.add_argument("--close", type=float, default=5.0,
                    help="metres from the start that counts as 'back' (5)")
    ap.add_argument("--move", type=float, default=2.0,
                    help="metres of travel before the anchor is set (2)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--fresh", type=float, default=90,
                    help="a GPS log counts as live only if written within this "
                         "many seconds; stops a finished log from a previous "
                         "session being mistaken for this run (default 90)")
    ap.add_argument("--replay", metavar="LOG",
                    help="score a finished GPS log instead of watching "
                         "live; use this to check the thresholds")
    a = ap.parse_args()

    need = LOOP_M * a.min_frac
    t_start = time.time()
    log = None

    if not a.quiet:
        print("[lap] loop %.1f m -> need %.1f m driven and a return within %.1f m"
              % (LOOP_M, need, a.close))
        print("[lap] waiting for a LIVE GPS log "
              "(written in the last %.0fs) ..." % a.fresh)

    if a.replay:
        log = os.path.expanduser(a.replay)
        print("[lap] REPLAY %s" % os.path.basename(log))

    while log is None and not a.replay:
        if time.time() - t_start > a.timeout:
            print("[lap] TIMEOUT: no GPS log ever appeared.")
            return 2
        log = newest_log(a.fresh)
        if log is None:
            time.sleep(1)
    if not a.quiet and log:
        print("[lap] watching %s" % os.path.basename(log))

    anchor = None
    travelled = 0.0
    laps_done = 0
    seen = 0
    last_print = 0.0
    armed = False          # must leave the anchor before it can return to it

    while True:
        if time.time() - t_start > a.timeout:
            print("[lap] TIMEOUT after %.0f s with %d lap(s) done, %.1f m driven."
                  % (a.timeout, laps_done, travelled))
            return 2

        track = read_track(log)
        if len(track) > seen:
            for i in range(max(1, seen), len(track)):
                px, py = track[i - 1][1], track[i - 1][2]
                cx, cy = track[i][1], track[i][2]
                step = math.hypot(cx - px, cy - py)
                # ignore obvious fix jumps; they would inflate the distance
                if step > 5.0:
                    continue
                travelled += step
                if anchor is None and travelled >= a.move:
                    anchor = (cx, cy)
                    travelled = 0.0
                    if not a.quiet:
                        print("[lap] moving - anchor set, counting from here")
                    continue
                if anchor is None:
                    continue
                d_home = math.hypot(cx - anchor[0], cy - anchor[1])
                if not armed and d_home > a.close * 2:
                    armed = True
                if armed and travelled >= need and d_home <= a.close:
                    laps_done += 1
                    print("[lap] LAP %d COMPLETE - %.1f m driven, back within %.1f m"
                          % (laps_done, travelled, d_home))
                    if laps_done >= a.laps:
                        return 0
                    anchor = (cx, cy)
                    travelled = 0.0
                    armed = False
            seen = len(track)

        if a.replay:
            cx, cy = (track[-1][1], track[-1][2]) if track else (0.0, 0.0)
            d_home = (math.hypot(cx - anchor[0], cy - anchor[1])
                      if anchor else float("nan"))
            print("[lap] replay: %d lap(s), %.1f m driven (need %.1f), "
                  "ended %.1f m from anchor, left-the-start=%s"
                  % (laps_done, travelled, need, d_home, armed))
            return 0 if laps_done >= a.laps else 2

        if not a.quiet and time.time() - last_print >= 10 and track:
            fix = track[-1][3] or "?"
            if anchor is None:
                print("[lap] waiting for movement (%.1f m so far, fix %s)"
                      % (travelled, fix))
            else:
                cx, cy = track[-1][1], track[-1][2]
                d_home = math.hypot(cx - anchor[0], cy - anchor[1])
                print("[lap] %.1f / %.1f m driven, %.1f m from start, fix %s%s"
                      % (travelled, need, d_home, fix,
                         "" if armed else "  (not yet left the start)"))
            last_print = time.time()
        time.sleep(1)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[lap] interrupted")
        sys.exit(3)
