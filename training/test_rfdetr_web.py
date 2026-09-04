import os
import cv2
import depthai as dai
import supervision as sv

from flask import Flask, Response
from collections import Counter
from inference import get_model
import threading
import time


# ============================================================
# SETTINGS
# ============================================================

# The Roboflow key is per-account and is NOT in this repo. Put it in
# ~/.survey_keys (chmod 600) and source it:
#   export ROBOFLOW_API_KEY=your-key
ROBOFLOW_API_KEY = os.environ.get("ROBOFLOW_API_KEY", "")

MODEL_ID = "krishna-visanakarrala/mae-148-project-model-1-rfdetr-small-t1"

PORT = 5001


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

latest_frame = None
frame_lock = threading.Lock()


# ============================================================
# LOAD MODEL
# ============================================================

print()
print("========================================")
print("Loading RF-DETR model...")
print("========================================")

model = get_model(
    model_id=MODEL_ID,
    api_key=ROBOFLOW_API_KEY
)

print("RF-DETR model loaded!")


# ============================================================
# CREATE OAK-D PIPELINE
# ============================================================

print()
print("========================================")
print("Creating OAK-D pipeline...")
print("========================================")

pipeline = dai.Pipeline()

camera = pipeline.create(dai.node.Camera)

video = camera.build(
    dai.CameraBoardSocket.CAM_A
).requestOutput(
    (1280, 720),
    dai.ImgFrame.Type.BGR888p
)

queue = video.createOutputQueue(
    maxSize=1,
    blocking=False
)

print("Camera pipeline created!")


# ============================================================
# SUPERVISION
# ============================================================

box_annotator = sv.BoxAnnotator()
label_annotator = sv.LabelAnnotator()


# ============================================================
# CAMERA + MODEL THREAD
# ============================================================

def process_camera():

    global latest_frame

    print()
    print("========================================")
    print("Connecting to OAK-D...")
    print("========================================")

    device = dai.Device()

    print("OAK-D connected!")
    print("Device:", device.getDeviceName())

    pipeline.start(device)

    print()
    print("========================================")
    print("RF-DETR LIVE TEST")
    print("========================================")
    print("Open on your Mac:")
    print()
    print(f"http://192.168.139.171:{PORT}")
    print()
    print("========================================")

    while True:

        frame_data = queue.get()

        if frame_data is None:
            continue

        frame = frame_data.getCvFrame()

        # ----------------------------------------------------
        # RF-DETR
        # ----------------------------------------------------

        try:

            results = model.infer(
                frame,
                confidence=0.35
            )

            result = results[0]

            detections = sv.Detections.from_inference(result)

        except Exception as e:

            print("Inference error:", e)
            continue


        # ----------------------------------------------------
        # DRAW BOXES
        # ----------------------------------------------------

        annotated = box_annotator.annotate(
            scene=frame.copy(),
            detections=detections
        )


        # ----------------------------------------------------
        # LABELS
        # ----------------------------------------------------

        labels = []

        for prediction in result.predictions:

            labels.append(
                f"{prediction.class_name} "
                f"{prediction.confidence:.2f}"
            )


        annotated = label_annotator.annotate(
            scene=annotated,
            detections=detections,
            labels=labels
        )


        # ----------------------------------------------------
        # COUNT OBJECTS
        # ----------------------------------------------------

        counts = Counter()

        for prediction in result.predictions:

            class_name = prediction.class_name.lower()

            counts[class_name] += 1


        # ----------------------------------------------------
        # DISPLAY COUNTS
        # ----------------------------------------------------

        cv2.rectangle(
            annotated,
            (10, 10),
            (330, 125),
            (0, 0, 0),
            -1
        )

        cv2.putText(
            annotated,
            f"PEOPLE: {counts.get('person', 0)}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2
        )

        cv2.putText(
            annotated,
            f"SHRUBS: {counts.get('shrub', 0)}",
            (20, 75),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2
        )

        cv2.putText(
            annotated,
            f"TREES: {counts.get('tree', 0)}",
            (20, 110),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2
        )


        # ----------------------------------------------------
        # JPEG ENCODE
        # ----------------------------------------------------

        success, buffer = cv2.imencode(
            ".jpg",
            annotated,
            [cv2.IMWRITE_JPEG_QUALITY, 80]
        )

        if not success:
            continue

        with frame_lock:

            latest_frame = buffer.tobytes()


# ============================================================
# WEB STREAM
# ============================================================

def generate_frames():

    global latest_frame

    while True:

        with frame_lock:

            frame = latest_frame

        if frame is None:

            time.sleep(0.05)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame
            + b"\r\n"
        )


# ============================================================
# WEB PAGE
# ============================================================

@app.route("/")
def index():

    return """
    <!DOCTYPE html>

    <html>

    <head>

        <title>OAK-D RF-DETR</title>

        <style>

            body {
                background: #111;
                color: white;
                font-family: Arial;
                text-align: center;
            }

            img {
                width: 90%;
                max-width: 1280px;
            }

        </style>

    </head>

    <body>

        <h1>OAK-D Lite + RF-DETR</h1>

        <img src="/video">

    </body>

    </html>
    """


@app.route("/video")
def video():

    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    camera_thread = threading.Thread(
        target=process_camera,
        daemon=True
    )

    camera_thread.start()

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
