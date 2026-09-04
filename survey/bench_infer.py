import os, time, numpy as np
os.environ.setdefault("MODEL_CACHE_DIR", os.path.expanduser("~/.inference_cache"))
n = os.environ.get("OMP_NUM_THREADS", "all")
t0 = time.time()
from inference import get_model
m = get_model(model_id="krishna-visanakarrala/mae-148-project-model-1-rfdetr-small-t1")
print(f"  model loaded in {time.time()-t0:.1f}s  (threads={n})", flush=True)
frame = (np.random.rand(360, 640, 3) * 255).astype(np.uint8)
m.infer(frame, confidence=0.40)          # warm-up
ts = []
for _ in range(5):
    t = time.time(); m.infer(frame, confidence=0.40); ts.append(time.time() - t)
ts.sort()
print(f"  inference: median {ts[2]*1000:6.0f} ms   -> {1/ts[2]:.2f} fps   (threads={n})", flush=True)
