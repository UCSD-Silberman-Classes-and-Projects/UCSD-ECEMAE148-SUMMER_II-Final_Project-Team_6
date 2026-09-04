#!/usr/bin/env python3
"""Score a detections JSON against YOLO ground-truth labels.

    python3 ~/score_vs_gt.py detections.json labels_dir [MINCONF]

Uses the SAME VOC all-point AP50 as float_reference.py, so results are directly
comparable to the float baselines measured in Colab. Stdlib only.
"""
import sys, os, json

NAMES = ["trees", "shrubs", "people"]


def load_gt(path, w, h):
    if not os.path.exists(path):
        return []
    gt = []
    for line in open(path):
        p = line.split()
        if len(p) < 5:
            continue
        c = int(p[0]); xc, yc, bw, bh = (float(v) for v in p[1:5])
        gt.append([c, (xc - bw / 2) * w, (yc - bh / 2) * h,
                   (xc + bw / 2) * w, (yc + bh / 2) * h])
    return gt


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def ap50(dets, gts, cls, thr=0.5):
    recs = [(k, d[1], d[2:]) for k, v in dets.items() for d in v if d[0] == cls]
    recs.sort(key=lambda r: -r[1])
    gt_by = {k: [g[1:] for g in v if g[0] == cls] for k, v in gts.items()}
    n_gt = sum(len(v) for v in gt_by.values())
    if n_gt == 0:
        return None, 0, 0, 0
    used = {k: [False] * len(v) for k, v in gt_by.items()}
    tp = [0.0] * len(recs); fp = [0.0] * len(recs)
    for i, (k, _, box) in enumerate(recs):
        best, bi = 0.0, -1
        for j, g in enumerate(gt_by.get(k, [])):
            v = iou(box, g)
            if v > best:
                best, bi = v, j
        if best >= thr and bi >= 0 and not used[k][bi]:
            tp[i] = 1.0; used[k][bi] = True
        else:
            fp[i] = 1.0
    ctp = cfp = 0.0; rec = []; prec = []
    for i in range(len(recs)):
        ctp += tp[i]; cfp += fp[i]
        rec.append(ctp / n_gt); prec.append(ctp / max(ctp + cfp, 1e-9))
    mrec = [0.0] + rec + [1.0]
    mpre = [0.0] + prec + [0.0]
    for i in range(len(mpre) - 2, -1, -1):
        mpre[i] = max(mpre[i], mpre[i + 1])
    ap = sum((mrec[i + 1] - mrec[i]) * mpre[i + 1]
             for i in range(len(mrec) - 1) if mrec[i + 1] != mrec[i])
    return ap, n_gt, len(recs), int(ctp)


def main():
    d = json.load(open(sys.argv[1]))
    labels_dir = sys.argv[2]
    floor = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    dets = {k: [x for x in v if x[1] >= floor] for k, v in d["detections"].items()}
    sizes = d["sizes"]
    gts = {k: load_gt(os.path.join(labels_dir, os.path.splitext(k)[0] + ".txt"),
                      sizes[k][0], sizes[k][1]) for k in dets}

    print(f"source     {d.get('source','?')}")
    print(f"preprocess {d.get('preprocess','?')}   conf floor {floor or d.get('conf','?')}")
    print(f"images     {len(dets)}   detections {sum(len(v) for v in dets.values())}")
    if d.get("fps"):
        print(f"throughput {d['fps']:.1f} fps")
    print()
    print(f"{'class':<8}{'AP50':>8}{'gt':>7}{'pred':>7}{'TP':>7}{'recall':>9}")
    aps = []
    for c, nm in enumerate(NAMES):
        ap, n_gt, n_pred, tp = ap50(dets, gts, c)
        if ap is None:
            print(f"{nm:<8}{'n/a':>8}{0:>7}{n_pred:>7}"); continue
        aps.append(ap)
        print(f"{nm:<8}{ap:>8.4f}{n_gt:>7}{n_pred:>7}{tp:>7}{tp/n_gt:>9.3f}")
    if aps:
        print(f"{'mAP50':<8}{sum(aps)/len(aps):>8.4f}")


main()
