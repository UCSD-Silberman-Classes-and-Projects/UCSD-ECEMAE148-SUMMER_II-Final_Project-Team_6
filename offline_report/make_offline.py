#!/usr/bin/env python3
"""Build a single self-contained HTML report for one survey run.

    python3 make_offline.py <run_id> <out.html> [jpeg_quality]

Everything is embedded: frames as base64, both reports, the narration, the GPS
track and the detection clusters. No network, no server, no fonts to fetch -
it has to work on a teammate's laptop with the Wi-Fi off.
"""
import base64, csv, glob, json, os, sys, time
import cv2
sys.path.insert(0, os.path.expanduser("~/oakd_project"))
import analyze_survey as A

RUN = sys.argv[1]
OUT = sys.argv[2]
Q = int(sys.argv[3]) if len(sys.argv) > 3 else 50
CLASSES = ["trees", "shrubs", "people"]


def read(p):
    try:
        with open(p) as fh:
            return fh.read()
    except OSError:
        return ""


# ---- frames -----------------------------------------------------------------
paths = sorted(glob.glob(os.path.join(A.DATA, "live", RUN, "a_*.jpg")))
if not paths:
    paths = sorted(glob.glob(os.path.join(A.FRAMES, RUN, "f_*.jpg")))
    annotated = False
else:
    annotated = True
frames, total = [], 0
for i, p in enumerate(paths):
    im = cv2.imread(p)
    if im is None:
        continue
    ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, Q])
    if not ok:
        continue
    b = buf.tobytes()
    total += len(b)
    frames.append(base64.b64encode(b).decode("ascii"))
    if (i + 1) % 50 == 0:
        print("  %d/%d frames (%.1f MB)" % (i + 1, len(paths), total / 1048576),
              flush=True)
print("  %d frames, %.1f MB of JPEG" % (len(frames), total / 1048576), flush=True)

# ---- detections, track, clusters --------------------------------------------
det_path = os.path.join(A.LOGS, "detections_%s.csv" % RUN)
dets, t_lo, t_hi = [], None, None
per_frame = {}
with open(det_path) as fh:
    for r in csv.DictReader(fh):
        try:
            t = float(r["unix_time"])
        except (TypeError, ValueError, KeyError):
            continue
        try:
            per_frame[int(r["frame_id"])] = per_frame.get(int(r["frame_id"]), 0) + 1
        except (TypeError, ValueError, KeyError):
            pass
        t_lo = t if t_lo is None else min(t_lo, t)
        t_hi = t if t_hi is None else max(t_hi, t)
        if r.get("utm_x") and r.get("utm_y"):
            try:
                dets.append([r["class"], float(r["utm_x"]), float(r["utm_y"])])
            except ValueError:
                pass
        elif r.get("x_utm") and r.get("y_utm"):
            try:
                dets.append([r["class"], float(r["x_utm"]), float(r["y_utm"])])
            except ValueError:
                pass

track = A.load_gps(t_lo, t_hi) if t_lo else []
print("  track %d samples, %d located detections" % (len(track), len(dets)), flush=True)

# Cluster with the same code the report used, so the map agrees with the counts.
spatial = [(c, x, y) for c, x, y in dets]
clusters = A.cluster(spatial, 3.0) if spatial else {}
cpts = [[c, round(k["x"], 2), round(k["y"], 2), k["n"]]
        for c, v in clusters.items() for k in v]

# thin the raw track for the map; the shape is what matters, not every sample
step = max(1, len(track) // 900)
tpts = [[round(s[1], 2), round(s[2], 2)] for s in track[::step]]

# ---- text -------------------------------------------------------------------
survey_text = read(os.path.join(A.REPORTS, "survey_%s.txt" % RUN))
llm_text = read(os.path.join(A.REPORTS, "llm_report_%s.txt" % RUN))
live = {}
try:
    live = json.load(open(os.path.join(A.DATA, "live", "%s.json" % RUN)))
except (OSError, ValueError):
    pass

counts = {c: len(v) for c, v in clusters.items()}
absorbed, emitted, net, _rows = A.carbon_summary(counts)

metres = 0.0
for i in range(1, len(track)):
    dx = track[i][1] - track[i - 1][1]
    dy = track[i][2] - track[i - 1][2]
    metres += (dx * dx + dy * dy) ** 0.5

# Open on the busiest frame, not frame 0: the camera is still ramping its
# exposure at the start of a lap, so frame 0 is a washed-out white rectangle -
# a poor first thing for a teammate to see.
start_idx = 0
if per_frame:
    best_id = max(per_frame, key=per_frame.get)
    for n, q in enumerate(paths):
        digits = "".join(ch for ch in os.path.basename(q) if ch.isdigit())
        if digits and int(digits) == best_id:
            start_idx = n
            break

payload = {
    "run": RUN,
    "start": start_idx,
    "when": time.strftime("%d %B %Y, %H:%M", time.localtime(t_lo or time.time())),
    "annotated": annotated,
    "n_frames": len(frames),
    "counts": counts,
    "raw": {c: sum(k["n"] for k in v) for c, v in clusters.items()},
    "carbon": {"absorbed": round(absorbed), "emitted": round(emitted),
               "net": round(net)},
    "metres": round(metres),
    "seconds": round((t_hi - t_lo) if t_lo and t_hi else 0),
    "backend": live.get("backend", ""),
    "rate": live.get("rate"),
    "narration": live.get("narration") or [],
    "survey_text": survey_text,
    "llm_text": llm_text,
    "track": tpts,
    "clusters": cpts,
}

tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "offline_template.html")).read()
html = (tpl.replace("/*__DATA__*/", json.dumps(payload))
           .replace("/*__FRAMES__*/", json.dumps(frames)))
with open(OUT, "w") as fh:
    fh.write(html)
print("wrote %s  (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1048576))
