#!/usr/bin/env python3
"""
export_dataset.py  --  turn past runs into YOLOv8 training data  (Claude, 2026-09-02)

The RF-DETR model has already drawn ~34,000 boxes on this car's own footage.
Those boxes ARE labels. Converting them into a YOLO dataset lets us train a
YOLOv8 that copies RF-DETR's behaviour - and unlike RF-DETR, YOLOv8 compiles to
the .hef the AI HAT runs.

This is knowledge distillation: the student can only be as good as the teacher,
so it inherits RF-DETR's mistakes. That is an acceptable trade here, because the
goal is the SAME detections ~100x faster, not better ones. Low-confidence boxes
are dropped so the student learns from the teacher's confident calls only.

  ./export_dataset.py --out ~/grove_dataset --min-conf 0.55
"""
import argparse
import csv
import glob
import os
import random
import shutil

DATA = os.path.expanduser("~/oakd_project/data")
FRAMES = os.path.join(DATA, "frames")
LOGS = os.path.join(DATA, "logs")
CLASSES = ["trees", "shrubs", "people"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/grove_dataset"))
    ap.add_argument("--min-conf", type=float, default=0.55)
    ap.add_argument("--val-split", type=float, default=0.15)
    ap.add_argument("--max-per-run", type=int, default=0,
                    help="cap frames per run (0 = all); keeps the set balanced")
    a = ap.parse_args()
    random.seed(0)

    # ---- gather boxes per (run, frame) --------------------------------------
    per_frame = {}
    kept = dropped = 0
    for path in sorted(glob.glob(os.path.join(LOGS, "detections_*.csv"))):
        run = os.path.basename(path)[len("detections_"):-4]
        fdir = os.path.join(FRAMES, run)
        if not os.path.isdir(fdir):
            continue
        try:
            with open(path) as fh:
                for r in csv.DictReader(fh):
                    try:
                        cls = r["class"]
                        conf = float(r["confidence"])
                        x1, y1 = float(r["x1"]), float(r["y1"])
                        x2, y2 = float(r["x2"]), float(r["y2"])
                        fid = int(r["frame_id"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if cls not in CLASSES:
                        continue
                    if conf < a.min_conf:
                        dropped += 1
                        continue
                    per_frame.setdefault((run, fid), []).append(
                        (CLASSES.index(cls), x1, y1, x2, y2))
                    kept += 1
        except OSError:
            continue

    print("boxes kept %d, dropped below %.2f: %d" % (kept, a.min_conf, dropped))
    print("frames with at least one box: %d" % len(per_frame))

    # ---- cap per run so one long run cannot dominate ------------------------
    by_run = {}
    for (run, fid) in per_frame:
        by_run.setdefault(run, []).append(fid)
    chosen = []
    for run, fids in sorted(by_run.items()):
        fids.sort()
        if a.max_per_run and len(fids) > a.max_per_run:
            step = len(fids) / float(a.max_per_run)
            fids = [fids[int(i * step)] for i in range(a.max_per_run)]
        print("  %-28s %5d frames" % (run, len(fids)))
        chosen += [(run, f) for f in fids]

    random.shuffle(chosen)
    n_val = int(len(chosen) * a.val_split)
    split = {"val": chosen[:n_val], "train": chosen[n_val:]}

    # ---- write it out -------------------------------------------------------
    if os.path.isdir(a.out):
        shutil.rmtree(a.out)
    for s in ("train", "val"):
        os.makedirs(os.path.join(a.out, s, "images"), exist_ok=True)
        os.makedirs(os.path.join(a.out, s, "labels"), exist_ok=True)

    try:
        import cv2
        def size_of(p):
            im = cv2.imread(p)
            return (im.shape[1], im.shape[0]) if im is not None else None
    except ImportError:
        from PIL import Image
        def size_of(p):
            try:
                return Image.open(p).size
            except Exception:
                return None

    written = 0
    for s, items in split.items():
        for run, fid in items:
            src = os.path.join(FRAMES, run, "f_%06d.jpg" % fid)
            if not os.path.exists(src) or os.path.getsize(src) == 0:
                continue
            wh = size_of(src)
            if not wh:
                continue
            W, H = wh
            lines = []
            for ci, x1, y1, x2, y2 in per_frame[(run, fid)]:
                cx = ((x1 + x2) / 2.0) / W
                cy = ((y1 + y2) / 2.0) / H
                bw = abs(x2 - x1) / float(W)
                bh = abs(y2 - y1) / float(H)
                if bw <= 0 or bh <= 0:
                    continue
                cx, cy = min(max(cx, 0), 1), min(max(cy, 0), 1)
                bw, bh = min(bw, 1), min(bh, 1)
                lines.append("%d %.6f %.6f %.6f %.6f" % (ci, cx, cy, bw, bh))
            if not lines:
                continue
            stem = "%s_%06d" % (run, fid)
            shutil.copy(src, os.path.join(a.out, s, "images", stem + ".jpg"))
            with open(os.path.join(a.out, s, "labels", stem + ".txt"), "w") as fh:
                fh.write("\n".join(lines) + "\n")
            written += 1

    with open(os.path.join(a.out, "data.yaml"), "w") as fh:
        fh.write("path: %s\ntrain: train/images\nval: val/images\n\n"
                 "nc: %d\nnames: %s\n" % (a.out, len(CLASSES), CLASSES))
    with open(os.path.join(a.out, "classes.txt"), "w") as fh:
        fh.write("\n".join(CLASSES) + "\n")

    print("\nwrote %d images to %s" % (written, a.out))
    for s in ("train", "val"):
        n = len(glob.glob(os.path.join(a.out, s, "images", "*.jpg")))
        print("  %-6s %5d images" % (s, n))
    print("\nNOTE: labels come from RF-DETR, so the trained model copies it -")
    print("      same detections, ~100x faster on the HAT, not more accurate.")


if __name__ == "__main__":
    main()
