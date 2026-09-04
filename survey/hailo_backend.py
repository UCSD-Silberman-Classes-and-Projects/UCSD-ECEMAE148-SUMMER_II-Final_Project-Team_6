#!/usr/bin/env python3
"""
hailo_backend.py  --  run detection on the AI HAT   (Claude, 2026-09-02)

Drop-in replacement for the CPU detector. Used automatically by live_survey.py
as soon as a compiled model exists at ~/oakd_project/models/grove.hef.

WHY A SEPARATE FILE: the Hailo runtime lives in the SYSTEM python
(`hailo_platform`), while the detector runs in ~/obj-detection-env. Keeping the
Hailo code isolated means the CPU path keeps working untouched if anything here
is missing.

The compiled model must be built for hailo8 (26 TOPS), NOT hailo8l, and with a
Dataflow Compiler matching the Pi's HailoRT (4.20.x). See Grove_HAT_model.ipynb.
"""
import os

MODEL_DIR = os.path.expanduser("~/oakd_project/models")
HEF_PATH = os.path.join(MODEL_DIR, "grove.hef")
LABELS_PATH = os.path.join(MODEL_DIR, "grove_classes.txt")

# Preprocessing MUST match the calibration the .hef was compiled with.
#   grove_lb.hef -> letterbox   (measured mAP50 0.795 on the float model)
#   grove.hef    -> plain resize(measured mAP50 0.618)
# Override for an A/B with  HAILO_LETTERBOX=0
LETTERBOX = os.environ.get("HAILO_LETTERBOX", "1") == "1"


def available():
    """True only if the accelerator, the runtime and a compiled model all exist."""
    if not (os.path.exists(HEF_PATH) and os.path.exists("/dev/hailo0")):
        return False
    try:
        import hailo_platform  # noqa: F401
    except ImportError:
        return False
    return True


def load_labels():
    try:
        with open(LABELS_PATH) as fh:
            return [l.strip() for l in fh if l.strip()]
    except OSError:
        return []


class Prediction(object):
    """Same shape as the inference package returns, so callers need no changes."""
    __slots__ = ("class_name", "confidence", "x", "y", "width", "height")

    def __init__(self, class_name, confidence, x, y, width, height):
        self.class_name = class_name
        self.confidence = confidence
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _Result(object):
    __slots__ = ("predictions",)

    def __init__(self, predictions):
        self.predictions = predictions


class HailoDetector(object):
    """YOLO-style detector on the Hailo-8, with NMS done on-chip.

    The model zoo HEFs emit a single output shaped (classes, 5, max_dets):
    each row is [y1, x1, y2, x2, score] in NORMALISED coordinates. We convert
    to pixel centre/size so the rest of the pipeline is unchanged.
    """

    def __init__(self, hef_path=HEF_PATH, labels=None):
        from hailo_platform import (VDevice, HEF, ConfigureParams,
                                    HailoStreamInterface, InputVStreamParams,
                                    OutputVStreamParams, FormatType, InferVStreams)
        self._InferVStreams = InferVStreams
        self.labels = labels or load_labels()
        self.hef = HEF(hef_path)
        self.vdev = VDevice()
        cfg = ConfigureParams.create_from_hef(
            self.hef, interface=HailoStreamInterface.PCIe)
        self.ng = self.vdev.configure(self.hef, cfg)[0]
        self.ngp = self.ng.create_params()
        self.ip = InputVStreamParams.make(self.ng, format_type=FormatType.UINT8)
        self.op = OutputVStreamParams.make(self.ng, format_type=FormatType.FLOAT32)
        vi = self.hef.get_input_vstream_infos()[0]
        self.iname = vi.name
        self.in_h, self.in_w = vi.shape[0], vi.shape[1]
        self._ctx = None
        self._pipe = None

    def __enter__(self):
        self._ctx = self.ng.activate(self.ngp)
        self._ctx.__enter__()
        self._pipe = self._InferVStreams(self.ng, self.ip, self.op)
        self._pipe.__enter__()
        return self

    def __exit__(self, *a):
        try:
            if self._pipe:
                self._pipe.__exit__(*a)
        finally:
            if self._ctx:
                self._ctx.__exit__(*a)

    def infer(self, img, confidence=0.40):
        """img: BGR frame from cv2. Returns an object with .predictions."""
        import cv2
        import numpy as np
        h, w = img.shape[:2]
        if LETTERBOX:
            s = min(self.in_w / float(w), self.in_h / float(h))
            nw, nh = int(round(w * s)), int(round(h * s))
            px, py = (self.in_w - nw) // 2, (self.in_h - nh) // 2
            canvas = np.full((self.in_h, self.in_w, 3), 114, np.uint8)
            canvas[py:py + nh, px:px + nw] = cv2.resize(img, (nw, nh))
            r = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        else:
            s, px, py = None, 0, 0
            r = cv2.cvtColor(cv2.resize(img, (self.in_w, self.in_h)),
                             cv2.COLOR_BGR2RGB)
        out = self._pipe.infer({self.iname: np.expand_dims(r, 0).astype(np.uint8)})
        det = list(out.values())[0][0]
        preds = []
        for ci, rows in enumerate(det):
            arr = np.array(rows)
            if arr.size == 0:
                continue
            name = self.labels[ci] if ci < len(self.labels) else str(ci)
            for d in arr:
                if len(d) < 5:
                    continue
                score = float(d[4])
                if score < confidence:
                    continue
                y1, x1, y2, x2 = (float(d[0]), float(d[1]),
                                  float(d[2]), float(d[3]))
                if LETTERBOX:
                    x1 = (x1 * self.in_w - px) / s
                    x2 = (x2 * self.in_w - px) / s
                    y1 = (y1 * self.in_h - py) / s
                    y2 = (y2 * self.in_h - py) / s
                else:
                    x1, x2 = x1 * w, x2 * w
                    y1, y2 = y1 * h, y2 * h
                preds.append(Prediction(name, score,
                                        (x1 + x2) / 2.0, (y1 + y2) / 2.0,
                                        abs(x2 - x1), abs(y2 - y1)))
        return _Result(preds)


def selftest():
    print("model  :", HEF_PATH, "-", "found" if os.path.exists(HEF_PATH) else "MISSING")
    print("device :", "/dev/hailo0", "-", "present" if os.path.exists("/dev/hailo0") else "MISSING")
    print("labels :", load_labels() or "MISSING (grove_classes.txt)")
    if not available():
        print("\nNot ready. Compile a model with Grove_HAT_model.ipynb, then:")
        print("  scp yolov8s.hef robocar:~/oakd_project/models/grove.hef")
        print("  scp classes.txt robocar:~/oakd_project/models/grove_classes.txt")
        return 1
    import glob, time, cv2
    frames = sorted(glob.glob(os.path.expanduser(
        "~/oakd_project/data/frames/*/f_*.jpg")))[-8:]
    if not frames:
        print("no recorded frames to test against")
        return 1
    with HailoDetector() as det:
        t0 = time.time()
        hits = {}
        for f in frames:
            res = det.infer(cv2.imread(f))
            for p in res.predictions:
                hits.setdefault(p.class_name, []).append(p.confidence)
        el = time.time() - t0
    print("\n%d frames in %.2fs -> %.1f fps" % (len(frames), el, len(frames) / el))
    for k, v in sorted(hits.items(), key=lambda x: -len(x[1])):
        print("  %-10s %3d hits, best %.2f" % (k, len(v), max(v)))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
