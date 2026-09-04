#!/usr/bin/env python3
"""Diff HAT (int8, grove.hef) detections against the float ONNX baseline.

    python3 ~/compare_reference.py float_reference.json hat_reference.json

Answers the question the self-test cannot: did int8 quantization cost accuracy,
and if so, where. Both files use the same schema:
    {"detections": {image: [[cls, score, x1, y1, x2, y2], ...]}, ...}
"""
import sys, json

NAMES = ["trees", "shrubs", "people"]


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    ref = json.load(open(sys.argv[1]))
    hat = json.load(open(sys.argv[2]))
    R, H = ref["detections"], hat["detections"]

    # The chip's NMS floor is 0.2, so the float side must be cut to the same
    # threshold or the comparison counts boxes the HAT was never allowed to emit.
    import os
    floor = float(os.environ.get("MINCONF", "0"))
    if floor > 0:
        R = {k: [d for d in v if d[1] >= floor] for k, v in R.items()}
        H = {k: [d for d in v if d[1] >= floor] for k, v in H.items()}
        print(f"float/HAT both filtered to conf >= {floor}")
    print(f"float preproc          {ref.get('preprocess','?')}")
    print(f"HAT   preproc          {hat.get('preprocess','?')}")

    shared = sorted(set(R) & set(H))
    if not shared:
        print("No overlapping images between the two files."); sys.exit(1)
    print(f"images compared        {len(shared)}")
    if len(R) != len(shared) or len(H) != len(shared):
        print(f"  (float had {len(R)}, HAT had {len(H)})")

    print(f"\n{'':<10}{'float':>8}{'HAT':>8}{'delta':>9}")
    for c, nm in enumerate(NAMES):
        nf = sum(1 for k in shared for d in R[k] if d[0] == c)
        nh = sum(1 for k in shared for d in H[k] if d[0] == c)
        pct = f"{(nh-nf)/nf*100:+.1f}%" if nf else "n/a"
        print(f"{nm:<10}{nf:>8}{nh:>8}{pct:>9}")
    tf = sum(len(R[k]) for k in shared)
    th = sum(len(H[k]) for k in shared)
    print(f"{'TOTAL':<10}{tf:>8}{th:>8}{(f'{(th-tf)/tf*100:+.1f}%' if tf else 'n/a'):>9}")

    # Box-level agreement: for each float detection, is there a HAT box of the
    # same class overlapping it?
    matched = missed = 0
    score_delta = []
    per_class_missed = {n: 0 for n in NAMES}
    for k in shared:
        used = [False] * len(H[k])
        for d in R[k]:
            best, bi = 0.0, -1
            for j, e in enumerate(H[k]):
                if used[j] or e[0] != d[0]:
                    continue
                v = iou(d[2:], e[2:])
                if v > best:
                    best, bi = v, j
            if best >= 0.5 and bi >= 0:
                used[bi] = True
                matched += 1
                score_delta.append(H[k][bi][1] - d[1])
            else:
                missed += 1
                per_class_missed[NAMES[d[0]]] += 1
    spurious = sum(len(H[k]) for k in shared) - matched

    print(f"\nfloat boxes matched by HAT (IoU>=0.5)   {matched}/{matched+missed}"
          f"  ({matched/(matched+missed)*100:.1f}%)" if matched + missed else "")
    print(f"float boxes the HAT missed              {missed}")
    for n, v in per_class_missed.items():
        if v:
            print(f"    {n}: {v}")
    print(f"HAT boxes with no float counterpart     {spurious}")
    if score_delta:
        score_delta.sort()
        mean = sum(score_delta) / len(score_delta)
        med = score_delta[len(score_delta) // 2]
        print(f"confidence shift on matched boxes       mean {mean:+.3f}  median {med:+.3f}")

    if ref.get("mAP50") is not None:
        print(f"\nfloat mAP50 (measured here)             {ref['mAP50']:.4f}")
        for nm, v in ref.get("per_class", {}).items():
            if v.get("AP50") is not None:
                print(f"    {nm:<8} AP50 {v['AP50']:.4f}  gt {v['n_gt']}")
    if hat.get("fps"):
        print(f"HAT throughput                          {hat['fps']:.1f} fps")

    print("\nRead it this way:")
    print("  recall within a few % and small confidence shift -> quantization is fine")
    print("  a class losing >15% of its boxes                 -> re-optimize at level 2")
    print("  near-zero HAT detections                         -> preprocessing bug,")
    print("     most likely the backend dividing by 255 when the HEF already does")


main()
