#!/usr/bin/env python3
"""
live_survey.py  --  detect WHILE the car is driving   (Claude, 2026-09-02)

The offline analyser reads the frame index once and processes a finished run.
This one tails the run as it is being recorded: every frame that has both a
complete JPEG on disk and a row in the index gets inferred immediately, so
detections and counts accumulate live and the dashboard can show them.

IT CANNOT OUTRUN THE PI. Measured inference is ~0.59 fps on two cores, so at a
recording rate above that it falls progressively behind and finishes after the
lap ends. That is still far better than waiting for the whole offline pass, and
the lag is reported honestly in the live file rather than hidden.

Writes:  data/live/<run>.json      running counts, progress and lag
         data/logs/detections_<run>.csv   same format as the offline analyser
         reports/survey_<run>.txt   + llm_report_<run>.txt at the end
"""
import argparse
import csv
import json
import os
import sys
import time

# ---------------------------------------------------------------------------
# CPU CAP, applied before torch/inference are imported so it actually binds.
# On 2026-09-02 this script ran unrestricted (max_workers=4) with the camera
# attached and browned the Pi out mid-test: the board negotiates a 3 A supply
# where a Pi 5 wants 5 A, and four-core inference plus the OAK-D exceeds it.
# sched_setaffinity is the hard limit; the env vars stop the libraries from
# spawning pools they cannot use anyway.
# ---------------------------------------------------------------------------
def _cap_cpu(n):
    try:
        total = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        total = os.cpu_count() or 1
    n = max(1, min(int(n), total))
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "ORT_NUM_THREADS", "TORCH_NUM_THREADS"):
        os.environ[var] = str(n)
    try:
        os.sched_setaffinity(0, set(range(n)))
    except (AttributeError, OSError):
        pass
    return n


_CORES = _cap_cpu(os.environ.get("SURVEY_CORES", 2))

sys.path.insert(0, os.path.expanduser("~/oakd_project"))
import analyze_survey as A

LIVE_DIR = os.path.join(A.DATA, "live")


def _load_keys(path="~/.survey_keys"):
    """Read the API keys ourselves.

    survey.sh sources this file, but a manual launch does not - and on
    2026-09-02 that silently cost a finished run its written report and all
    its live narration. Loading it here means every launch path works.
    """
    p = os.path.expanduser(path)
    try:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:]
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    except OSError:
        pass


_load_keys()


# BGR (OpenCV order) of the dashboard's validated palette, so a box on the
# video is the same colour as that class everywhere else on the page:
#   trees  #0a7a45   shrubs #d9a03e   people #6f5bd0
BOX_BGR = {"trees": (69, 122, 10), "shrubs": (62, 160, 217),
           "people": (208, 91, 111)}


def _annotate(cv2, img, preds, out_path, archive_path=None):
    """Save the frame with its boxes drawn, so the dashboard can show what the
    detector is actually seeing rather than only the numbers it produced.

    archive_path keeps a numbered copy so a finished live session can be
    replayed later. Only `out_path` was written before, which meant the whole
    run collapsed to whichever frame happened to be last."""
    try:
        vis = img.copy()
        for pred in preds:
            cls = pred.class_name
            cx, cy, w, h = pred.x, pred.y, pred.width, pred.height
            x1, y1 = int(cx - w / 2), int(cy - h / 2)
            x2, y2 = int(cx + w / 2), int(cy + h / 2)
            col = BOX_BGR.get(cls, (200, 200, 200))
            cv2.rectangle(vis, (x1, y1), (x2, y2), col, 2)
            label = "%s %.0f%%" % (cls, float(pred.confidence) * 100)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(vis, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), col, -1)
            cv2.putText(vis, label, (x1 + 3, max(9, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1,
                        cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if not ok:
            return
        data = buf.tobytes()
        # Encode once, write twice: the archived copy and the one the MJPEG
        # stream watches. Both land via os.replace so a reader never sees a
        # half-written JPEG.
        for dest in ((archive_path,) if archive_path else ()) + (out_path,):
            tmp = dest + ".tmp.jpg"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)
    except Exception:
        pass          # a drawing failure must never stop the survey


# 75 s produced about one line on a two-minute lap, which reads as broken.
NARRATE_EVERY = float(os.environ.get("NARRATE_EVERY", 30))


def narrate(counts, processed, recorded, metres, elapsed, prior):
    """A short running commentary while the lap is in progress.

    Deliberately throttled and deliberately small: one or two sentences on what
    has changed. It reports only the numbers it is given - the model is told
    plainly that these are cluster counts of the CAR's position, not surveyed
    objects, so the commentary cannot imply more precision than the data has.
    Never raises: a narration failure must not disturb the survey.
    """
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    tally = ", ".join("%d %s" % (v["distinct"], k) for k, v in sorted(counts.items())) or "nothing yet"
    prompt = (
        "You are narrating a robot's tree survey as it drives, for a live "
        "dashboard. Write ONE short sentence (max 22 words) on the current "
        "state. Be factual and plain, no marketing tone, no exclamation marks.\n\n"
        "Distinct objects so far: %s\n"
        "Frames analysed: %d of %d recorded\n"
        "Distance driven: %.0f m\n"
        "Elapsed: %d s\n"
        "Previous line you wrote (do not repeat it): %s\n\n"
        "These counts are spatial clusters of the ROVER's own position when a "
        "detection fired, not surveyed object positions. Do not claim species, "
        "health, or exact locations."
        % (tally, processed, recorded, metres, elapsed, prior or "(none)")
    )
    try:
        c = OpenAI(api_key=key, timeout=20.0)
        r = c.responses.create(model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
                               input=prompt, max_output_tokens=1500)
        # A reasoning model spends output_tokens on reasoning BEFORE it writes
        # anything. Measured on 2026-09-03: 156-261 reasoning tokens for a
        # two-class tally, and a three-class tally reliably blew the old 400
        # cap - the call "succeeded" with status=completed and an EMPTY
        # output_text, so every live narration silently came back blank.
        line = (r.output_text or "").strip()
        return line or None
    except Exception:
        return None


def track_metres(track):
    if not track or len(track) < 2:
        return 0.0
    import math as _m
    return sum(_m.hypot(track[i][1] - track[i-1][1], track[i][2] - track[i-1][2])
               for i in range(1, len(track)))


def summarise(clusters):
    """cluster() gives {class: [ {x,y,n}, ... ]}. Turn that into the
    distinct/raw pairs the report and the dashboard both expect."""
    out = {}
    for cls, bucket in clusters.items():
        out[cls] = {"distinct": len(bucket),
                    "raw": sum(c["n"] for c in bucket)}
    return out


def complete_jpeg(path):
    """A frame still being written has no end-of-image marker yet."""
    try:
        if os.path.getsize(path) < 512:
            return False
        with open(path, "rb") as fh:
            fh.seek(-2, os.SEEK_END)
            return fh.read() == b"\xff\xd9"
    except OSError:
        return False


def index_rows(run):
    """filename -> (frame_id, unix_time) from the recorder's index."""
    out = {}
    path = os.path.join(A.LOGS, "frames_%s.csv" % run)
    try:
        with open(path) as fh:
            for r in csv.DictReader(fh):
                try:
                    out[r["filename"]] = (r["frame_id"], float(r["unix_time"]))
                except (TypeError, ValueError, KeyError):
                    continue
    except OSError:
        pass
    return out


def recorder_alive():
    me = str(os.getpid())
    try:
        pids = os.listdir("/proc")
    except OSError:
        return False
    for pid in pids:
        if not pid.isdigit() or pid == me:
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                if b"record_survey.py" in fh.read():
                    return True
        except OSError:
            continue
    return False


def newest_run(fresh=90):
    """The run being recorded RIGHT NOW - never a finished one.

    On 2026-09-02 this returned the previous day's run because the recorder
    had not created today's directory yet, and the detector then re-processed
    that finished run and truncated its detection log. A run only counts if
    its directory has been written to within `fresh` seconds, i.e. frames are
    actively landing in it. Same rule the GPS log and the lap watcher use.
    """
    try:
        ds = [d for d in os.listdir(A.FRAMES)
              if os.path.isdir(os.path.join(A.FRAMES, d))]
    except OSError:
        return None
    now = time.time()
    for d in sorted(ds, reverse=True):
        try:
            if now - os.path.getmtime(os.path.join(A.FRAMES, d)) <= fresh:
                return d
        except OSError:
            continue
    return None


def write_live(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="run id (default: newest)")
    ap.add_argument("--radius", type=float, default=3.0)
    ap.add_argument("--conf", type=float, default=0.25,
                help="detection floor. Measured on the HAT over the 261-image "
                     "val split: recall at 0.2 is already only 0.87 for trees, "
                     "so 0.40 loses real objects on a counting survey.")
    ap.add_argument("--idle", type=float, default=25,
                    help="stop this many seconds after the recorder ends and "
                         "no new frames appear")
    ap.add_argument("--wait", type=float, default=180,
                    help="how long to wait for a run to start")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--narrate", action="store_true",
                    help="short LLM commentary every %ds while driving"
                         % int(NARRATE_EVERY))
    ap.add_argument("--cores", type=int, default=None,
                    help="CPU cores for inference (default 2; more risks a "
                         "brownout on the 3 A supply)")
    a = ap.parse_args()
    cores = _cap_cpu(a.cores) if a.cores else _CORES
    print("  cpu cap: %d core(s)" % cores, flush=True)

    os.environ.setdefault("MODEL_CACHE_DIR", os.path.expanduser("~/.inference_cache"))
    os.environ.setdefault("OFFLINE_MODE", "True")

    t_wait = time.time()
    run = a.run
    while not run:
        run = newest_run()
        if run and not os.path.exists(os.path.join(A.LOGS, "frames_%s.csv" % run)):
            run = None
        if not run:
            if time.time() - t_wait > a.wait:
                print("No run started within %.0fs." % a.wait, flush=True)
                return 2
            time.sleep(1)

    frame_dir = os.path.join(A.FRAMES, run)
    os.makedirs(LIVE_DIR, exist_ok=True)
    live_path = os.path.join(LIVE_DIR, "%s.json" % run)
    annot_path = os.path.join(LIVE_DIR, "%s_latest.jpg" % run)
    annot_dir = os.path.join(LIVE_DIR, run)
    try:
        os.makedirs(annot_dir, exist_ok=True)
    except OSError:
        annot_dir = None
    print("live analysis of run %s" % run, flush=True)

    import cv2

    # ---- pick the fastest detector that is actually available --------------
    # The AI HAT is ~19x the CPU, so it wins whenever a compiled model for our
    # classes exists. No flag to remember: drop grove.hef into models/ and the
    # next run uses it. Falls back to the CPU automatically if anything is
    # missing, so a half-finished HAT setup can never break a survey.
    detector = None
    backend = "cpu"
    try:
        sys.path.insert(0, os.path.expanduser("~/oakd_project"))
        import hailo_backend
        if hailo_backend.available():
            print("  loading model on the AI HAT ...", flush=True)
            detector = hailo_backend.HailoDetector().__enter__()
            backend = "hailo"
            print("  HAT ready (%s)" % ", ".join(hailo_backend.load_labels()),
                  flush=True)
        else:
            print("  AI HAT idle: no %s yet - using the CPU"
                  % os.path.basename(hailo_backend.HEF_PATH), flush=True)
    except Exception as e:
        print("  AI HAT unavailable (%s: %s) - using the CPU"
              % (type(e).__name__, e), flush=True)

    if detector is None:
        from inference import get_model
        print("  loading model on the CPU ...", flush=True)
        detector = get_model(model_id=A.MODEL_ID)
        print("  model ready", flush=True)
    model = detector

    det_path = os.path.join(A.LOGS, "detections_%s.csv" % run)
    # Guard: opening "w" truncates. If this run already has a detection log and
    # nothing is recording into it, we are about to destroy a finished result.
    # A zero-byte log is not a finished result - it is the header-less remains
    # of a detector that died on startup (that is what the numpy 2 / pyhailort
    # crash left behind on 2026-09-03). Refusing on those strands the run with
    # no way forward, so only protect a log that actually holds something.
    try:
        has_result = os.path.getsize(det_path) > 0
    except OSError:
        has_result = False
    if has_result and not recorder_alive():
        print("REFUSING to overwrite %s - that run is already analysed and "
              "nothing is recording. Pass a different --run." % det_path,
              flush=True)
        return 3
    det_file = open(det_path, "w", newline="")
    dw = csv.writer(det_file)
    dw.writerow(["frame_id", "unix_time", "class", "confidence",
                 "x1", "y1", "x2", "y2", "utm_x", "utm_y", "fix"])

    # first frame timestamp of THIS run: the lower bound for its GPS window
    _run_t0 = None
    _idx0 = index_rows(run)
    if _idx0:
        _run_t0 = min(v[1] for v in _idx0.values()) - 5

    seen = set()
    skipped = set()          # frames deferred while keeping the feed live
    spatial = []
    n_det = 0
    no_fix = 0
    track = []
    t_track = 0.0
    t_last_new = time.time()
    t0 = time.time()
    t_narr = time.time()
    narration = []
    last_line = None

    while True:
        # The GPS log grows during the lap, so refresh it periodically - but
        # SCOPED to this run. load_gps() with no window concatenates every log
        # the car has ever written, which reported a 108 m lap as 750 m in the
        # live narration on 2026-09-02. Detections were unaffected (matching is
        # +/-2 s) but every distance was wrong.
        if time.time() - t_track > 5:
            _lo = _run_t0 or (time.time() - 3600)
            track = A.load_gps(_lo, time.time() + 5)
            t_track = time.time()

        idx = index_rows(run)
        try:
            on_disk = sorted(f for f in os.listdir(frame_dir) if f.startswith("f_"))
        except OSError:
            on_disk = []

        # ---- NEWEST-FIRST while the car is still driving --------------
        # Processing oldest-first means that once inference falls behind the
        # recorder, the feed shows where the car WAS, not where it is. While
        # recording is live we always take the newest unprocessed frame and
        # set the rest aside; the backlog is analysed once recording stops, so
        # nothing is lost and the picture stays current.
        pending = [f for f in on_disk if f not in seen and f in idx]
        live_now = recorder_alive()
        if live_now and pending:
            todo = [pending[-1]]            # only the freshest frame
            skipped.update(pending[:-1])
        else:
            # caught up / recording finished: work through the backlog in order
            todo = sorted(skipped | set(pending))
            skipped.clear()
        for fname in todo:
            full = os.path.join(frame_dir, fname)
            if not complete_jpeg(full):
                continue            # still being written; catch it next pass
            fid, ftime = idx[fname]
            if _run_t0 is None or ftime - 5 < _run_t0:
                _run_t0 = ftime - 5
            img = cv2.imread(full)
            seen.add(fname)
            if img is None:
                continue
            t_last_new = time.time()

            res = model.infer(img, confidence=a.conf)
            res = res[0] if isinstance(res, list) else res
            preds = list(getattr(res, "predictions", []))
            arch = (os.path.join(annot_dir, fname.replace("f_", "a_", 1))
                    if annot_dir else None)
            _annotate(cv2, img, preds, annot_path, arch)
            for pred in preds:
                cls = pred.class_name
                cf = float(pred.confidence)
                cx, cy, w, h = pred.x, pred.y, pred.width, pred.height
                fix = A.nearest_fix(track, ftime)
                if fix is None:
                    no_fix += 1
                    ux = uy = ""
                    fixname = ""
                else:
                    _, ux, uy, fixname = fix
                    spatial.append((cls, ux, uy))
                dw.writerow([fid, "%.3f" % ftime, cls, "%.3f" % cf,
                             "%.0f" % (cx - w / 2), "%.0f" % (cy - h / 2),
                             "%.0f" % (cx + w / 2), "%.0f" % (cy + h / 2),
                             ux, uy, fixname])
                n_det += 1
            det_file.flush()

            counts = summarise(A.cluster(spatial, a.radius)) if spatial else {}
            done, total = len(seen), len(on_disk)
            el = time.time() - t0

            if a.narrate and (time.time() - t_narr) >= NARRATE_EVERY:
                t_narr = time.time()
                line = narrate(counts, done, total, track_metres(track),
                               int(el), last_line)
                if line:
                    last_line = line
                    narration.append({"t": int(el), "text": line})
                    narration[:] = narration[-12:]
            write_live(live_path, {
                "run": run, "processed": done, "recorded": total,
                "behind": max(0, total - done),
                "deferred": len(skipped),
                "mode": "live" if live_now else "catching up",
                "raw": n_det,
                "counts": counts, "no_fix": no_fix,
                "rate": round(done / el, 2) if el > 0 else 0,
                "recording": recorder_alive(),
                "annotated": os.path.basename(annot_path),
                "backend": backend,
                "narration": narration,
                "elapsed": int(el), "updated": time.time(),
            })
            if done % 10 == 0:
                print("  %d/%d frames | %d det | %.2f fps | %d behind"
                      % (done, total, n_det, done / el if el else 0,
                         max(0, total - done)), flush=True)

        if not recorder_alive() and (time.time() - t_last_new) > a.idle:
            break
        time.sleep(0.4)

    det_file.close()
    counts = summarise(A.cluster(spatial, a.radius)) if spatial else {}
    el = time.time() - t0
    write_live(live_path, {
        "run": run, "processed": len(seen), "recorded": len(seen),
        "behind": 0, "raw": n_det, "counts": counts, "no_fix": no_fix,
        "rate": round(len(seen) / el, 2) if el else 0, "recording": False,
        "elapsed": int(el), "updated": time.time(), "finished": True,
        "narration": narration, "mode": "finished", "deferred": 0,
        "backend": backend,
    })

    os.makedirs(A.REPORTS, exist_ok=True)
    lines = ["=" * 56,
             "SURVEY RESULTS  -  run %s  (live)" % run,
             "=" * 56, "",
             "Frames analysed : %d  (live, during the lap)" % len(seen),
             "Analysis time   : %.1f s at %.2f fps" % (el, len(seen) / el if el else 0),
             "Raw detections  : %d" % n_det, "",
             "DISTINCT OBJECTS (merged within %.1f m):" % a.radius]
    for cls in sorted(counts):
        c = counts[cls]
        lines.append("    %-10s %3d distinct   (%d raw detections)"
                     % (cls, c["distinct"], c["raw"]))
    lines += A.carbon_lines({c: v["distinct"] for c, v in counts.items()})
    lines += A.positioning_lines(track)
    lines += ["", "METHOD / LIMITATIONS",
              "  Detection ran WHILE the car drove, on frames as they were",
              "  recorded. Counts are distinct spatial clusters of the CAR's",
              "  position when each detection fired, not surveyed positions of",
              "  the objects themselves. No depth was used, so two objects",
              "  passed within %.1f m may merge. Treat counts as a lower bound." % a.radius,
              "", "Detection log: %s" % det_path]
    text = "\n".join(lines)
    out = os.path.join(A.REPORTS, "survey_%s.txt" % run)
    with open(out, "w") as fh:
        fh.write(text + "\n")
    print(text, flush=True)
    print("Saved -> %s" % out, flush=True)

    if a.llm:
        facts = text + "\nGPS samples recorded: %d\n" % len(track)
        A.llm_report(run, facts, os.path.join(A.REPORTS, "llm_report_%s.txt" % run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
