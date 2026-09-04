#!/usr/bin/env python3
"""
analyze_survey.py  --  MAE 148 Team 6

Runs AFTER the lap. Takes the recorded frames plus the GPS track and produces
the survey results: a GPS-tagged detection log and de-duplicated counts.

Why counting changed:
    The live app used ByteTrack, which re-identifies an object as "new" every
    time the track fragments. On a moving car that happens constantly, which
    is how a 4.6-minute lap reported 59 trees and 56 shrubs.

    Here every detection carries the car's UTM position from the GPS log, so
    the same tree seen across consecutive frames collapses into one cluster.
    The number reported is "distinct clusters", which is a claim the data
    actually supports.

Honest limitation, state it in the report:
    We log where the CAR was, not where the tree is. Without depth we cannot
    place the object itself, so two objects passed within --radius of each
    other can merge. Cluster counts are a lower bound.

Usage:
    ./analyze_survey.py --list
    ./analyze_survey.py --run 20260831_180108_lap1
    ./analyze_survey.py --run <id> --radius 4.0 --conf 0.40
"""

import argparse
import csv
import glob
import json
import os
import sys
import time

DATA = os.path.expanduser("~/oakd_project/data")
FRAMES = os.path.join(DATA, "frames")
LOGS = os.path.join(DATA, "logs")
REPORTS = os.path.expanduser("~/oakd_project/reports")
CKPT = os.path.join(DATA, "checkpoints")

MODEL_ID = os.getenv(
    "ROBOFLOW_MODEL_ID",
    "krishna-visanakarrala/mae-148-project-model-1-rfdetr-small-t1",
)


# ---- carbon ---------------------------------------------------------------
# Population averages per object per year. These are NOT measurements of the
# specific plants this rover drove past, and the counts they multiply are
# route-segment clusters, so treat the result as an order-of-magnitude figure.
#   tree   21.8 kg/yr  - the common urban-forestry average (~48 lb/yr)
#   shrub   3.0 kg/yr  - least standardised of the three; varies enormously
#   human 365.0 kg/yr  - respiration at roughly 1 kg CO2/day
# If you change these, change the copy in survey_web.py too.
CO2_KG_PER_YEAR = {"trees": 21.8, "shrubs": 3.0, "people": 365.0}
CO2_ABSORBS = ("trees", "shrubs")          # the rest are treated as emitting


def positioning_lines(track):
    """What the GPS actually reported during the run.

    The logger only stamps a fix name on loops where it parsed a GGA sentence
    (~1 Hz), while the drive loop runs far faster - so most rows carry a
    position with a BLANK name. Blank does NOT mean "no fix", and reports that
    omitted this made the written report hedge that centimetre accuracy could
    not be claimed even when the receiver was RTK-fixed the whole way.
    """
    if not track:
        return []
    named = {}
    for row in track:
        nm = row[3] if len(row) > 3 else ""
        if nm:
            named[nm] = named.get(nm, 0) + 1
    total_named = sum(named.values())
    out = ["", "POSITIONING"]
    if not total_named:
        out.append("  %d samples, none carried a fix quality." % len(track))
        return out
    best = max(named, key=named.get)
    out.append("  %d GPS samples; %d carried a parsed fix quality:"
               % (len(track), total_named))
    for nm in sorted(named, key=named.get, reverse=True):
        out.append("    %-10s %5d  (%.0f%% of parsed)"
                   % (nm, named[nm], 100.0 * named[nm] / total_named))
    out.append("  Dominant fix: %s. The unparsed samples still carry positions;"
               % best)
    out.append("  a blank fix name means no GGA sentence that loop, not a lost fix.")
    return out


def carbon_summary(counts):
    """counts: {class: n}. Returns (absorbed_kg, emitted_kg, net_kg, rows)."""
    rows, absorbed, emitted = [], 0.0, 0.0
    for cls in sorted(counts):
        n = counts[cls]
        rate = CO2_KG_PER_YEAR.get(cls)
        if not n or rate is None:
            continue
        kg = n * rate
        if cls in CO2_ABSORBS:
            absorbed += kg
        else:
            emitted += kg
        rows.append((cls, n, rate, kg, cls in CO2_ABSORBS))
    return absorbed, emitted, absorbed - emitted, rows


def carbon_lines(counts):
    """The CARBON block appended to a survey report."""
    absorbed, emitted, net, rows = carbon_summary(counts)
    if not rows:
        return []
    out = ["", "CARBON  (rough, per year)"]
    for cls, n, rate, kg, absorbs in rows:
        out.append("    %-10s %3d x %6.1f kg  =  %8.0f kg CO2 %s"
                   % (cls, n, rate, kg, "absorbed" if absorbs else "emitted"))
    out += ["    " + "-" * 48,
            "    %-10s %27s %8.0f kg CO2 / yr" % ("net", "", net),
            "",
            "  Population averages per object, not measurements of these",
            "  plants: tree 21.8 kg/yr (~48 lb, urban-forestry average),",
            "  shrub 3 kg/yr (least standardised), human respiration 1 kg/day.",
            "  Human respiration is part of the SHORT carbon cycle - it returns",
            "  carbon that food crops recently took from the air - so the net",
            "  figure is illustrative, not a climate ledger.",
            "  Counts are route-segment clusters, so this inherits their error."]
    return out


def list_runs():
    runs = sorted(glob.glob(os.path.join(FRAMES, "*")))
    if not runs:
        print("No recorded runs found in", FRAMES)
        return
    print("Recorded runs:")
    for r in runs:
        n = len(glob.glob(os.path.join(r, "*.jpg")))
        print("  %-34s %5d frames" % (os.path.basename(r), n))


def load_frames(run):
    path = os.path.join(LOGS, "frames_%s.csv" % run)
    if not os.path.exists(path):
        sys.exit("No frame index at %s" % path)
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    good = []
    for r in rows:
        # A power loss mid-write leaves a truncated final line. Skip it
        # instead of crashing the whole analysis. (Claude, 2026-08-31)
        try:
            r["unix_time"] = float(r["unix_time"])
        except (TypeError, ValueError):
            continue
        good.append(r)
    if len(good) != len(rows):
        print("  skipped %d malformed row(s) in frame index"
              % (len(rows) - len(good)))
    return good


def load_gps(lo=None, hi=None, pad=5.0):
    """The GPS samples belonging to ONE run.

    Every log the car has ever written lives in the same directory, so without
    a time window this returns every session at once. Detections still matched
    correctly (matching is +/-2 s, and an old session can never be that close),
    but the SUMMARY was wrong: on 2026-09-01 a 106.7 m lap was reported as a
    284.6 m track because the previous evening's 159.5 m was added to it, and
    the fix-quality histogram was likewise a mix of two sessions.
    """
    out = []
    for p in sorted(glob.glob(os.path.join(os.path.expanduser("~/gpscar/logs"),
                                           "gps_*.csv"))):
        try:
            with open(p) as fh:
                for r in csv.DictReader(fh):
                    try:
                        _t = float(r["unix_time"])
                        if lo is not None and not (lo - pad <= _t <= hi + pad):
                            continue
                        out.append((_t,
                                    float(r["pos_x"]),
                                    float(r["pos_y"]),
                                    r.get("fix_name", "")))
                    except (ValueError, KeyError):
                        continue
        except OSError:
            continue
    out.sort()
    return out


def nearest_fix(track, t, tol=2.0):
    """Closest GPS sample to time t, or None if nothing within tol seconds."""
    if not track:
        return None
    lo, hi = 0, len(track) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if track[mid][0] < t:
            lo = mid + 1
        else:
            hi = mid
    best = min((track[i] for i in (lo - 1, lo, lo + 1) if 0 <= i < len(track)),
               key=lambda s: abs(s[0] - t))
    return best if abs(best[0] - t) <= tol else None


def cluster(dets, radius):
    """Greedy spatial clustering per class. dets: (cls, x, y). Returns counts."""
    clusters = {}
    for cls, x, y in dets:
        bucket = clusters.setdefault(cls, [])
        for c in bucket:
            if (x - c["x"]) ** 2 + (y - c["y"]) ** 2 <= radius * radius:
                c["n"] += 1
                c["x"] += (x - c["x"]) / c["n"]      # running centroid
                c["y"] += (y - c["y"]) / c["n"]
                break
        else:
            bucket.append({"x": x, "y": y, "n": 1})
    return clusters


def llm_report(run, facts_text, out_path):
    """One LLM call over the FINISHED survey numbers. Never raises."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        print()
        print("  LLM report skipped: OPENAI_API_KEY is not set.")
        print("  Run:  export OPENAI_API_KEY=sk-...   then re-run with --llm")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        print("  LLM report skipped: openai package not installed.")
        return None

    prompt = """You are writing the survey section of an undergraduate
engineering report for an autonomous rover (UCSD MAE 148).

Here are the measured results. Every number below is real:

%s

Write a concise survey report with these sections:
1. What the rover surveyed and for how long
2. What was found (use the distinct-object counts, not the raw detections)
3. Positioning quality and what it means for the results
4. Limitations, stated plainly
5. A one-paragraph overall assessment

Rules you must follow:
- Use ONLY the numbers given above. Do not invent objects, distances,
  species, areas, or measurements.
- The counts come from clustering the ROVER'S OWN position at the moment of
  each detection. They are NOT surveyed positions of the objects themselves.
  Say so plainly in the limitations section.
- Treat the counts as a lower bound, because two objects passed within the
  merge radius collapse into one.
- If the positioning was RTK fixed, note that this is centimetre-grade.
  If it was anything less, say the positions are correspondingly coarser.
- Do not claim conclusions about vegetation health, species, or
  environmental quality. Nothing in the data supports that.
- Under 350 words. Plain professional prose, no marketing tone.
""" % facts_text

    model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    try:
        client = OpenAI(api_key=key, timeout=40.0)
        resp = client.responses.create(model=model, input=prompt,
                                       max_output_tokens=2000)
        summary = resp.output_text
    except Exception as e:
        print()
        print("  LLM request failed: %s: %s" % (type(e).__name__, e))
        return None

    try:
        with open(out_path, "w") as fh:
            fh.write(summary + "\n")
    except OSError as e:
        print("  Could not save LLM report:", e)

    print()
    print("=" * 56)
    print("LLM SURVEY REPORT  (model: %s)" % model)
    print("=" * 56)
    print(summary)
    print("=" * 56)
    print("Saved -> %s" % out_path)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--radius", type=float, default=3.0,
                    help="metres; detections closer than this merge (default 3)")
    ap.add_argument("--conf", type=float, default=0.40)
    ap.add_argument("--every", type=int, default=1,
                    help="analyse every Nth frame. Recording is 4 fps, so "
                         "4 = 1 fps, 8 = 0.5 fps. Consecutive frames are "
                         "near-duplicates and the merge radius dedupes them "
                         "anyway.")
    ap.add_argument("--restart", action="store_true",
                    help="ignore any saved checkpoint and start over")
    ap.add_argument("--llm", action="store_true",
                    help="also generate the LLM survey report (needs OPENAI_API_KEY)")
    args = ap.parse_args()

    if args.list or not args.run:
        list_runs()
        return 0

    frame_dir = os.path.join(FRAMES, args.run)
    if not os.path.isdir(frame_dir):
        sys.exit("No such run: %s" % frame_dir)

    frames = load_frames(args.run)
    _n_recorded = len(frames)
    # Take the window from the FULL frame list, before subsampling drops the tail.
    _t_lo = min(f["unix_time"] for f in frames) if frames else None
    _t_hi = max(f["unix_time"] for f in frames) if frames else None
    if args.every > 1:
        _before = len(frames)
        frames = frames[::args.every]
        print("  subsample  : every %d -> %d of %d frames"
              % (args.every, len(frames), _before))
    track = load_gps(_t_lo, _t_hi)
    os.makedirs(REPORTS, exist_ok=True)

    print("=" * 56)
    print("SURVEY ANALYSIS")
    print("=" * 56)
    print("  run        : %s" % args.run)
    print("  frames     : %d" % len(frames))
    print("  gps samples: %d %s" % (len(track),
                                    "" if track else "<- none, counts cannot be de-duplicated"))
    print("  merge radius: %.1f m" % args.radius)
    print()

    os.environ.setdefault("MODEL_CACHE_DIR", os.path.expanduser("~/.inference_cache"))
    os.environ.setdefault("OFFLINE_MODE", "True")
    import cv2
    from inference import get_model

    print("  loading model...", flush=True)
    model = get_model(model_id=MODEL_ID)
    print("  model ready\n", flush=True)

    det_path = os.path.join(LOGS, "detections_%s.csv" % args.run)

    # ---- checkpoint / resume (Claude, 2026-08-31) ----------------------
    # The Pi loses power under sustained load. Every 25 frames we atomically
    # write progress so a brownout costs 25 frames instead of the whole run.
    os.makedirs(CKPT, exist_ok=True)
    _cp = os.path.join(CKPT, "%s_every%d.json" % (args.run, args.every))
    _start_i = 0
    spatial = []
    per_frame_max = {}
    n_det = 0
    no_fix = 0
    if args.restart and os.path.exists(_cp):
        os.remove(_cp)
        print("  --restart: discarded saved checkpoint", flush=True)
    if os.path.exists(_cp):
        try:
            with open(_cp) as _fh:
                _d = json.load(_fh)
            _start_i = int(_d["next_index"])
            spatial = [tuple(x) for x in _d["spatial"]]
            per_frame_max = dict(_d["per_frame_max"])
            n_det = int(_d["n_det"])
            no_fix = int(_d["no_fix"])
            print("  RESUMING at frame %d/%d (%d detections already logged)"
                  % (_start_i, len(frames), n_det), flush=True)
        except Exception as e:
            print("  checkpoint unreadable (%s), starting over" % e, flush=True)
            _start_i = 0
            spatial, per_frame_max, n_det, no_fix = [], {}, 0, 0

    det_file = open(det_path, "a" if _start_i else "w", newline="")
    dw = csv.writer(det_file)
    if not _start_i:
        dw.writerow(["frame_id", "unix_time", "class", "confidence",
                     "x1", "y1", "x2", "y2", "utm_x", "utm_y", "fix"])

    def _save_ckpt(next_index):
        """Atomic: write to .tmp then rename, so a power cut mid-write
        cannot leave a half-written checkpoint behind."""
        _tmp = _cp + ".tmp"
        with open(_tmp, "w") as _fh:
            json.dump({"next_index": next_index, "spatial": spatial,
                       "per_frame_max": per_frame_max, "n_det": n_det,
                       "no_fix": no_fix}, _fh)
            _fh.flush()
            os.fsync(_fh.fileno())
        os.replace(_tmp, _cp)

    t0 = time.time()

    for i, fr in enumerate(frames):
        if i < _start_i:
            continue
        img = cv2.imread(os.path.join(frame_dir, fr["filename"]))
        if img is None:
            continue

        res = model.infer(img, confidence=args.conf)
        res = res[0] if isinstance(res, list) else res

        counts_here = {}
        for pred in getattr(res, "predictions", []):
            cls = pred.class_name
            conf = float(pred.confidence)
            cx, cy, w, h = pred.x, pred.y, pred.width, pred.height
            fix = nearest_fix(track, fr["unix_time"])
            if fix is None:
                no_fix += 1
                ux = uy = ""
                fname = ""
            else:
                _, ux, uy, fname = fix
                spatial.append((cls, ux, uy))

            dw.writerow([fr["frame_id"], "%.3f" % fr["unix_time"], cls,
                         "%.3f" % conf,
                         "%.0f" % (cx - w / 2), "%.0f" % (cy - h / 2),
                         "%.0f" % (cx + w / 2), "%.0f" % (cy + h / 2),
                         ux, uy, fname])
            counts_here[cls] = counts_here.get(cls, 0) + 1
            n_det += 1

        for cls, c in counts_here.items():
            per_frame_max[cls] = max(per_frame_max.get(cls, 0), c)

        if (i + 1) % 25 == 0 or i + 1 == len(frames):
            el = time.time() - t0
            rate = (i + 1 - _start_i) / el if el else 0
            eta = (len(frames) - i - 1) / rate if rate else 0
            print("  %4d/%d frames | %.2f fps | %d detections | eta %.0fs"
                  % (i + 1, len(frames), rate, n_det, eta), flush=True)
            det_file.flush()
            os.fsync(det_file.fileno())
            _save_ckpt(i + 1)

    det_file.close()
    if os.path.exists(_cp):
        os.remove(_cp)   # finished cleanly, no resume needed
    elapsed = time.time() - t0

    clusters = cluster(spatial, args.radius) if spatial else {}

    lines = []
    lines.append("=" * 56)
    lines.append("SURVEY RESULTS  -  run %s" % args.run)
    lines.append("=" * 56)
    lines.append("")
    _pct = (100.0 * len(frames) / _n_recorded) if _n_recorded else 0.0
    if args.every > 1:
        lines.append("Frames analysed : %d of %d recorded  (%.0f%%, every %dth frame)"
                     % (len(frames), _n_recorded, _pct, args.every))
    else:
        lines.append("Frames analysed : %d  (100%% of recorded frames)" % len(frames))
    lines.append("Analysis time   : %.1f s at %.2f fps" % (elapsed, len(frames) / elapsed))
    lines.append("Raw detections  : %d" % n_det)
    lines.append("")
    if clusters:
        lines.append("DISTINCT OBJECTS (merged within %.1f m):" % args.radius)
        for cls in sorted(clusters):
            lines.append("    %-10s %3d distinct   (%d raw detections)"
                         % (cls, len(clusters[cls]),
                            sum(c["n"] for c in clusters[cls])))
    else:
        lines.append("NO GPS TRACK - cannot de-duplicate.")
        lines.append("Most seen in any single frame (a safe lower bound):")
        for cls in sorted(per_frame_max):
            lines.append("    %-10s %3d" % (cls, per_frame_max[cls]))
    if no_fix:
        lines.append("")
        lines.append("%d detections had no GPS sample within 2 s and were "
                     "excluded from clustering." % no_fix)
    if clusters:
        lines += carbon_lines({c: len(v) for c, v in clusters.items()})
    lines += positioning_lines(track)
    lines.append("")
    lines.append("METHOD / LIMITATIONS")
    lines.append("  Counts are distinct spatial clusters of the CAR's position")
    lines.append("  when each detection was made, not surveyed positions of the")
    lines.append("  objects themselves. No depth was used, so two objects passed")
    lines.append("  within %.1f m may merge. Treat counts as a lower bound." % args.radius)
    if args.every > 1:
        lines.append("  Only every %dth recorded frame was analysed (%d of %d)."
                     % (args.every, len(frames), _n_recorded))
        lines.append("  An object visible only in skipped frames is not counted,")
        lines.append("  which pushes these counts further toward a lower bound.")
    lines.append("")
    lines.append("Detection log: %s" % det_path)

    text = "\n".join(lines)
    print()
    print(text)

    out = os.path.join(REPORTS, "survey_%s.txt" % args.run)
    with open(out, "w") as fh:
        fh.write(text + "\n")
    print()
    print("Saved -> %s" % out)

    if args.llm:
        # Facts block: measured values only, so the model has nothing to invent.
        dur_min = ((frames[-1]["unix_time"] - frames[0]["unix_time"]) / 60.0
                   if len(frames) > 1 else 0.0)
        fix_counts = {}
        for _t, _x, _y, _fx in track:
            if _fx:
                fix_counts[_fx] = fix_counts.get(_fx, 0) + 1
        travelled = 0.0
        for _i in range(1, len(track)):
            travelled += ((track[_i][1] - track[_i - 1][1]) ** 2 +
                          (track[_i][2] - track[_i - 1][2]) ** 2) ** 0.5

        f = []
        f.append("Run identifier: %s" % args.run)
        f.append("Survey duration: %.2f minutes" % dur_min)
        if args.every > 1:
            f.append("Camera frames analysed: %d of %d recorded (%.0f%% - every "
                     "%dth frame). Recording ran at 4 fps and much of the run was "
                     "stationary, so skipped frames are largely duplicates, but an "
                     "object seen only in a skipped frame is missed."
                     % (len(frames), _n_recorded, _pct, args.every))
        else:
            f.append("Camera frames recorded and analysed: %d (100%% of them)"
                     % len(frames))
        f.append("Raw detections before de-duplication: %d" % n_det)
        f.append("Detection confidence threshold: %.2f" % args.conf)
        f.append("Merge radius used for de-duplication: %.1f m" % args.radius)
        if clusters:
            f.append("Distinct objects after spatial clustering:")
            for cls in sorted(clusters):
                f.append("  %s: %d distinct (from %d raw detections)"
                         % (cls, len(clusters[cls]),
                            sum(c["n"] for c in clusters[cls])))
        else:
            f.append("No GPS track was available, so counts could NOT be "
                     "de-duplicated. Highest count seen in any single frame, "
                     "which is a safe lower bound:")
            for cls in sorted(per_frame_max):
                f.append("  %s: %d" % (cls, per_frame_max[cls]))
        f.append("GPS samples recorded: %d" % len(track))
        f.append("Distance travelled along the GPS track: %.1f m" % travelled)
        if fix_counts:
            f.append("GPS fix quality distribution (samples per mode):")
            for k in sorted(fix_counts, key=lambda z: -fix_counts[z]):
                f.append("  %s: %d" % (k, fix_counts[k]))
        else:
            f.append("GPS fix quality: not recorded.")
        f.append("Detections with no GPS sample within 2 s (excluded): %d" % no_fix)
        f.append("Detector: RF-DETR, classes people / shrubs / trees, run "
                 "offline after the lap rather than live on the vehicle.")

        llm_out = os.path.join(REPORTS, "llm_report_%s.txt" % args.run)
        llm_report(args.run, "\n".join(f), llm_out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
