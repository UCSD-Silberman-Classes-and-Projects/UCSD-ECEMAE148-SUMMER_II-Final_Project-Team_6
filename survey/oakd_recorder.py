import depthai as dai
import cv2
import base64
import requests
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ============================================================
# CONFIG
# ============================================================

ROBOFLOW_API_KEY = "YOUR_API_KEY_HERE"

MODEL = "chair-trees-classification-3-vit-base-patch16-224-in21k-t1"

PORT = 8080

INFERENCE_INTERVAL = 0.5

# ============================================================
# GLOBALS
# ============================================================

latest_frame = None
latest_result = "WAITING"
latest_confidence = 0.0

frame_lock = threading.Lock()

running = True

inference_running = False


# ============================================================
# ROBOFLOW
# ============================================================

def classify_image(frame):

    global latest_result
    global latest_confidence
    global inference_running

    if inference_running:
        return

    inference_running = True

    try:

        # JPEG encode
        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, 85]
        )

        if not success:
            return

        # Base64 encode
        image_base64 = base64.b64encode(
            buffer.tobytes()
        ).decode("utf-8")

        # ====================================================
        # CORRECT ROBOFLOW CLASSIFICATION ENDPOINT
        # ====================================================

        url = (
            "https://classify.roboflow.com/"
            + MODEL
            + "?api_key="
            + ROBOFLOW_API_KEY
        )

        # Roboflow classification API expects the
        # base64 image in the request body.
        response = requests.post(
            url,
            data=image_base64,
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=10
        )

        print(
            "Roboflow HTTP:",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "Roboflow error:",
                response.status_code,
                response.text
            )

            return

        result = response.json()

        print("Response:", result)

        predictions = result.get(
            "predictions",
            {}
        )

        if not predictions:

            latest_result = "UNKNOWN"
            latest_confidence = 0.0

            return

        # Find highest-confidence class
        best_class = None
        best_confidence = -1.0

        for class_name, prediction in predictions.items():

            confidence = float(
                prediction.get(
                    "confidence",
                    0.0
                )
            )

            if confidence > best_confidence:

                best_class = class_name
                best_confidence = confidence

        if best_class:

            latest_result = best_class.upper()
            latest_confidence = best_confidence

            print(
                "Prediction:",
                latest_result,
                f"{latest_confidence * 100:.1f}%"
            )

    except Exception as e:

        print(
            "Inference error:",
            repr(e)
        )

    finally:

        inference_running = False


# ============================================================
# WEB SERVER
# ============================================================

class CameraHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        global latest_frame
        global latest_result
        global latest_confidence

        # ----------------------------------------------------
        # MAIN PAGE
        # ----------------------------------------------------

        if self.path == "/":

            html = f"""
<!DOCTYPE html>

<html>

<head>

<title>OAK-D AI Camera</title>

<meta name="viewport"
content="width=device-width, initial-scale=1">

<meta http-equiv="refresh"
content="1">

<style>

body {{
    background: #111;
    color: white;
    font-family: Arial;
    text-align: center;
    margin: 0;
    padding: 20px;
}}

h1 {{
    font-size: 32px;
}}

.prediction {{
    font-size: 48px;
    font-weight: bold;
    margin: 20px;
}}

.confidence {{
    font-size: 25px;
    margin-bottom: 20px;
}}

img {{
    width: 90%;
    max-width: 1200px;
    border-radius: 10px;
}}

</style>

</head>

<body>

<h1>OAK-D AI Camera</h1>

<div class="prediction">
{latest_result}
</div>

<div class="confidence">
Confidence: {latest_confidence * 100:.1f}%
</div>

<img src="/latest.jpg">

</body>

</html>
"""

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/html"
            )

            self.end_headers()

            self.wfile.write(
                html.encode()
            )

            return

        # ----------------------------------------------------
        # CAMERA IMAGE
        # ----------------------------------------------------

        if self.path == "/latest.jpg":

            with frame_lock:

                frame = latest_frame

                if frame is None:

                    self.send_response(404)
                    self.end_headers()

                    return

                success, buffer = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 85]
                )

            if not success:

                self.send_response(500)
                self.end_headers()

                return

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "image/jpeg"
            )

            self.send_header(
                "Cache-Control",
                "no-cache"
            )

            self.end_headers()

            self.wfile.write(
                buffer.tobytes()
            )

            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):

        pass


# ============================================================
# WEB SERVER
# ============================================================

def start_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        CameraHandler
    )

    print()
    print("==============================")
    print("       OAK-D AI SERVER")
    print("==============================")
    print()
    print(
        "Web server: http://0.0.0.0:8080"
    )
    print()
    print(
        "Open on your Mac:"
    )
    print(
        "http://192.168.139.171:8080"
    )
    print()
    print("Press CTRL+C to stop.")
    print()

    server.serve_forever()


# ============================================================
# CAMERA
# ============================================================

def camera_loop():

    global latest_frame
    global running

    print("Connecting to OAK-D...")

    pipeline = dai.Pipeline()

    cam = pipeline.create(
        dai.node.ColorCamera
    )

    cam.setBoardSocket(
        dai.CameraBoardSocket.CAM_A
    )

    cam.setResolution(
        dai.ColorCameraProperties.SensorResolution.THE_1080_P
    )

    cam.setColorOrder(
        dai.ColorCameraProperties.ColorOrder.BGR
    )

    cam.setVideoSize(
        1920,
        1080
    )

    xout = pipeline.create(
        dai.node.XLinkOut
    )

    xout.setStreamName("rgb")

    cam.video.link(
        xout.input
    )

    with dai.Device(pipeline) as device:

        print("OAK-D connected.")

        queue = device.getOutputQueue(
            name="rgb",
            maxSize=4,
            blocking=False
        )

        last_inference = 0

        while running:

            packet = queue.tryGet()

            if packet is None:

                time.sleep(0.001)

                continue

            frame = packet.getCvFrame()

            # Save latest camera frame
            with frame_lock:

                latest_frame = frame.copy()

            # Run classification every 0.5 seconds
            now = time.time()

            if (
                now - last_inference
                >= INFERENCE_INTERVAL
            ):

                last_inference = now

                inference_frame = frame.copy()

                threading.Thread(
                    target=classify_image,
                    args=(inference_frame,),
                    daemon=True
                ).start()


# ============================================================
# MAIN
# ============================================================

def main():

    global running

    server_thread = threading.Thread(
        target=start_server,
        daemon=True
    )

    server_thread.start()

    time.sleep(1)

    try:

        camera_loop()

    except KeyboardInterrupt:

        print()
        print("Stopping...")

    except Exception as e:

        print(
            "Camera error:",
            repr(e)
        )

    finally:

        running = False

        print("Done.")


if __name__ == "__main__":

    main()