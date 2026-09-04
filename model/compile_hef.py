"""Rebuild grove.hef with LETTERBOX calibration.

Measured on the 261-image val split with the float ONNX at conf 0.001:
    plain resize  mAP50 0.6182
    letterbox     mAP50 0.7946
Calibration statistics must match what the camera pipeline actually feeds, so
the quantized model is calibrated on letterboxed frames. The Pi must letterbox
too - the two have to agree.

Everything else is identical to compile3.py, which produced a working HEF.
"""
import glob, os, json, re
import numpy as np
import cv2
import onnx
from hailo_sdk_client import ClientRunner

ONNX = "/content/src/best.onnx"
IMGSZ = 640
HEF_OUT = "/content/grove_lb.hef"
HAR_OUT = "/content/grove_lb_q.har"


def letterbox(bgr):
    """Aspect-preserving resize + centred 114-grey pad, matching inference."""
    h, w = bgr.shape[:2]
    r = min(IMGSZ / w, IMGSZ / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    px, py = (IMGSZ - nw) // 2, (IMGSZ - nh) // 2
    canvas = np.full((IMGSZ, IMGSZ, 3), 114, np.uint8)
    canvas[py:py + nh, px:px + nw] = cv2.resize(bgr, (nw, nh))
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)


# --- end nodes straight from the ONNX; the zoo YAML does not carry them -------
pat = re.compile(r"^/model\.22/(cv2|cv3)\.(\d)/(cv2|cv3)\.(\d)\.2/Conv$")
hits = [n.name for n in onnx.load(ONNX).graph.node if pat.match(n.name)]
order = sorted(hits, key=lambda s: (pat.match(s).group(2), pat.match(s).group(1)))
print("END NODES:", order, flush=True)

runner = ClientRunner(hw_arch="hailo8")
runner.translate_onnx_model(ONNX, "grove",
                            net_input_shapes={"images": [1, 3, IMGSZ, IMGSZ]},
                            end_node_names=order)
print("TRANSLATED", flush=True)

hnj = runner.get_hn()
hnj = json.loads(hnj) if isinstance(hnj, str) else hnj
L = hnj["layers"]
outs = sorted([k for k in L if L[k].get("type") == "output_layer"],
              key=lambda k: int(re.sub(r"\D", "", k) or 0))
srcs = [L[k]["input"][0].split("/")[-1] for k in outs]
print("SRC CONVS:", srcs, flush=True)

j = json.load(open("/content/hmz/hailo_model_zoo/cfg/postprocess_config/yolov8s_nms_config.json"))
j["classes"] = 3
j["bbox_decoders"] = [{"name": "bbox_decoder%d" % i, "stride": [8, 16, 32][i],
                       "reg_layer": srcs[2 * i], "cls_layer": srcs[2 * i + 1]}
                      for i in range(3)]
json.dump(j, open("/content/nms3.json", "w"), indent=1)

alls = "normalization1 = normalization([0.0, 0.0, 0.0], [255.0, 255.0, 255.0])\n"
alls += "".join("change_output_activation(%s, sigmoid)\n" % srcs[2 * i + 1] for i in range(3))
alls += 'nms_postprocess("/content/nms3.json", meta_arch=yolov8, engine=cpu)\n'
open("/content/grove_lb.alls", "w").write(alls)
runner.load_model_script("/content/grove_lb.alls")
print("SCRIPT LOADED", flush=True)

files = sorted(glob.glob("/content/calib/*.jpg"))[:128]
arr = np.stack([letterbox(cv2.imread(f)) for f in files]).astype(np.float32)
print("CALIB", arr.shape, "letterboxed", flush=True)

runner.optimize(arr)
runner.save_har(HAR_OUT)
print("OPTIMISED", flush=True)

hef = runner.compile()
open(HEF_OUT, "wb").write(hef)
print("WROTE", HEF_OUT, os.path.getsize(HEF_OUT), flush=True)
