#!/usr/bin/env python3

import time
import threading

import cv2
import depthai as dai
from flask import Flask, Response


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

latest_frame = None
frame_lock = threading.Lock()


# ============================================================
# CAMERA SETUP
# ============================================================

print("========================================")
print("OAK-D LITE CAMERA")
print("DepthAI 3.9")
print("========================================")

print("Creating pipeline...")

pipeline = dai.Pipeline()

camera = pipeline.create(dai.node.Camera)

output = camera.build(
    dai.CameraBoardSocket.CAM_A
).requestOutput(
    (640, 480),
    dai.ImgFrame.Type.BGR888p
)

queue = output.createOutputQueue()

print("Pipeline created!")

print("Starting OAK-D...")

# IMPORTANT:
# Do NOT create dai.Device().
# DepthAI 3.9 starts the device through pipeline.start().
pipeline.start()

print("OAK-D connected!")
print("Camera pipeline running!")

print("========================================")
print("WEB CAMERA")
print("========================================")
print("Open from your Mac:")
print("http://192.168.139.171:5000")
print("========================================")


# ============================================================
# CAMERA THREAD
# ============================================================

def camera_loop():

    global latest_frame

    print("Camera thread started!")

    while True:

        try:

            frame = queue.get()

            image = frame.getCvFrame()

            success, encoded = cv2.imencode(
                ".jpg",
                image
            )

            if success:

                with frame_lock:
                    latest_frame = encoded.tobytes()

        except Exception as e:

            print("Camera error:", e)

            time.sleep(0.5)


# ============================================================
# MJPEG STREAM
# ============================================================

def generate_frames():

    while True:

        with frame_lock:
            frame = latest_frame

        if frame is not None:

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame
                + b"\r\n"
            )

        time.sleep(0.03)


# ============================================================
# WEB PAGE
# ============================================================

@app.route("/")
def index():

    return """
    <!DOCTYPE html>

    <html>

    <head>

        <title>OAK-D Lite</title>

        <style>

            body {
                background: #111;
                color: white;
                font-family: Arial;
                text-align: center;
                margin: 0;
                padding: 20px;
            }

            h1 {
                margin-bottom: 20px;
            }

            img {
                width: 90%;
                max-width: 900px;
                border: 2px solid white;
            }

        </style>

    </head>

    <body>

        <h1>OAK-D Lite Live Camera</h1>

        <img src="/video">

    </body>

    </html>
    """


# ============================================================
# VIDEO ROUTE
# ============================================================

@app.route("/video")
def video():

    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    camera_thread = threading.Thread(
        target=camera_loop,
        daemon=True
    )

    camera_thread.start()

    print("Starting Flask server...")

    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True
    )
