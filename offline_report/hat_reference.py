#!/usr/bin/env python3
"""Run the deployed .hef over the validation split on the Pi and dump detections
in the same JSON shape as float_reference.py, so the two can be diffed.

    python3 ~/hat_reference.py [val_images_dir] [out.json]
    CONF=0.2  HAILO_LETTERBOX=1  python3 ~/hat_reference.py

Written against the real hailo_backend.py API:
    HailoDetector() is a context manager
    .infer(bgr, confidence) -> obj with .predictions
    Prediction = (class_name, confidence, x, y, width, height)  centre+size,
                 already mapped back to ORIGINAL image pixels.
The backend does its own preprocessing, so hand it the raw BGR frame.
"""
import os, sys, json, glob, time

VAL = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/grove_dataset/val/images")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/hat_reference.json")
CONF = float(os.environ.get("CONF", "0.2"))   # chip NMS floor is 0.2; go no lower
NAMES = ["trees", "shrubs", "people"]

sys.path.insert(0, os.path.expanduser("~/oakd_project"))
import cv2
import hailo_backend as hb


def main():
    if not hb.available():
        print("HAT not ready:"); hb.selftest(); sys.exit(1)
    if not os.path.isdir(VAL):
        print("no val dir at", VAL); sys.exit(1)

    imgs = sorted(glob.glob(os.path.join(VAL, "*.jpg")) +
                  glob.glob(os.path.join(VAL, "*.png")))
    print("images   :", len(imgs))
    print("conf     :", CONF)
    print("letterbox:", hb.LETTERBOX, flush=True)

    dets, sizes = {}, {}
    with hb.HailoDetector() as det:
        t0 = time.time()
        for n, f in enumerate(imgs):
            bgr = cv2.imread(f)
            if bgr is None:
                continue
            h, w = bgr.shape[:2]
            key = os.path.basename(f)
            rows = []
            for p in det.infer(bgr, confidence=CONF).predictions:
                ci = NAMES.index(p.class_name) if p.class_name in NAMES else None
                if ci is None:
                    continue
                rows.append([ci, float(p.confidence),
                             p.x - p.width / 2.0, p.y - p.height / 2.0,
                             p.x + p.width / 2.0, p.y + p.height / 2.0])
            dets[key] = rows
            sizes[key] = [w, h]
            if (n + 1) % 50 == 0:
                print("  %d/%d  %.1f fps" % (n + 1, len(imgs),
                                             (n + 1) / (time.time() - t0)), flush=True)
        el = time.time() - t0

    json.dump({"source": "grove.hef on Hailo-8",
               "preprocess": "letterbox" if hb.LETTERBOX else "resize",
               "conf": CONF, "names": NAMES, "n_images": len(dets),
               "fps": len(dets) / el if el else None,
               "sizes": sizes, "detections": dets}, open(OUT, "w"))
    print("\n%d images, %d detections, %.1f fps (includes jpeg decode)"
          % (len(dets), sum(len(v) for v in dets.values()), len(dets) / el))
    print("wrote", OUT)


main()
