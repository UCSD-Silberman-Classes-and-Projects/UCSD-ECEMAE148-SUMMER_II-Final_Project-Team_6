#!/usr/bin/env python3

import os
import cv2
import time
import threading
from collections import defaultdict

import depthai as dai
import supervision as sv
from flask import Flask, Response, jsonify
from inference import get_model
from openai import OpenAI


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_ID = os.getenv(
    "ROBOFLOW_MODEL_ID",
    "krishna-visanakarrala/mae-148-project-model-1-rfdetr-small-t1"
)

CLASS_NAMES = [
    "people",
    "shrubs",
    "trees"
]

CONF_THRESHOLD = 0.40

# Smaller camera image = much faster streaming/inference
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 360

# Camera can capture quickly while RF-DETR processes frames
CAMERA_FPS = 30

# Queue only keeps newest frame.
# This prevents lag from old frames piling up.
QUEUE_SIZE = 1

# Process RF-DETR every Nth frame.
#
# 1 = maximum detection frequency
# 2 = better FPS
# 3 = even faster camera response
#
# ByteTrack continues using the detections we provide.
INFERENCE_EVERY_N_FRAMES = 2

# ByteTrack parameters
TRACK_ACTIVATION_THRESHOLD = 0.35
LOST_TRACK_BUFFER = 30
MINIMUM_MATCHING_THRESHOLD = 0.70

FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5001

# OpenAI model used ONLY after survey ends
OPENAI_MODEL = "gpt-5.6-luna"


# ============================================================
# GLOBAL STATE
# ============================================================

state_lock = threading.Lock()

latest_jpeg = None

current_frame_counts = defaultdict(int)

seen_ids_by_class = defaultdict(set)

total_frames = 0
inference_frames = 0

survey_start_time = None
survey_end_time = None

last_error = None

stop_event = threading.Event()


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# CAMERA / RF-DETR WORKER
# ============================================================

def camera_worker():

    global latest_jpeg
    global total_frames
    global inference_frames
    global survey_start_time
    global survey_end_time
    global last_error

    print()
    print("========================================")
    print("LOADING RF-DETR")
    print("========================================")

    print("Model:", MODEL_ID)

    try:
        model = get_model(model_id=MODEL_ID)
    except Exception as e:
        last_error = str(e)

        print()
        print("========================================")
        print("RF-DETR LOAD ERROR")
        print("========================================")
        print(e)
        print()

        stop_event.set()
        return

    print("RF-DETR loaded successfully!")

    # --------------------------------------------------------
    # BYTE TRACK
    # --------------------------------------------------------

    print()
    print("========================================")
    print("CREATING BYTETRACK")
    print("========================================")

    try:

        tracker = sv.ByteTrack(
            track_activation_threshold=TRACK_ACTIVATION_THRESHOLD,
            lost_track_buffer=LOST_TRACK_BUFFER,
            minimum_matching_threshold=MINIMUM_MATCHING_THRESHOLD,
            frame_rate=CAMERA_FPS,
        )

        print("ByteTrack created!")

    except Exception as e:

        last_error = str(e)

        print("ByteTrack error:", e)

        stop_event.set()
        return

    # --------------------------------------------------------
    # ANNOTATORS
    # --------------------------------------------------------

    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator()

    # --------------------------------------------------------
    # CREATE OAK-D PIPELINE
    # --------------------------------------------------------

    print()
    print("========================================")
    print("CREATING OAK-D PIPELINE")
    print("========================================")

    try:

        pipeline = dai.Pipeline()

        camera = pipeline.create(dai.node.Camera)

        video = camera.build(
            dai.CameraBoardSocket.CAM_A
        ).requestOutput(
            (CAMERA_WIDTH, CAMERA_HEIGHT),
            dai.ImgFrame.Type.BGR888p
        )

        queue = video.createOutputQueue(
            maxSize=QUEUE_SIZE,
            blocking=False
        )

        print("Camera pipeline created!")

    except Exception as e:

        last_error = str(e)

        print()
        print("========================================")
        print("PIPELINE CREATION ERROR")
        print("========================================")
        print(e)

        stop_event.set()
        return

    # --------------------------------------------------------
    # START PIPELINE
    #
    # IMPORTANT:
    # DepthAI 3.9 works with pipeline.start().
    #
    # DO NOT USE:
    # dai.Device(pipeline)
    #
    # --------------------------------------------------------

    print()
    print("========================================")
    print("STARTING OAK-D")
    print("========================================")

    try:

        pipeline.start()

        print("OAK-D connected!")
        print("Camera streaming!")

    except Exception as e:

        last_error = str(e)

        print()
        print("========================================")
        print("OAK-D CAMERA ERROR")
        print("========================================")
        print(e)

        stop_event.set()
        return

    # --------------------------------------------------------
    # SURVEY START
    # --------------------------------------------------------

    survey_start_time = time.time()

    print()
    print("========================================")
    print("STARTING RF-DETR INFERENCE")
    print("========================================")

    print("Camera resolution:",
          CAMERA_WIDTH,
          "x",
          CAMERA_HEIGHT)

    print("Camera FPS:", CAMERA_FPS)

    print("Inference every:",
          INFERENCE_EVERY_N_FRAMES,
          "frame(s)")

    print()
    print("Open from your Mac:")
    print(
        f"http://192.168.139.171:{FLASK_PORT}/"
    )

    print()
    print("Press CTRL+C to stop the survey.")
    print("The OpenAI summary will be generated AFTER stopping.")
    print()

    frame_number = 0

    last_detections = sv.Detections.empty()

    last_detection_names = []
    last_detection_confidences = []
    last_detection_ids = []

    # --------------------------------------------------------
    # MAIN CAMERA LOOP
    # --------------------------------------------------------

    try:

        while not stop_event.is_set():

            # ------------------------------------------------
            # GET ONLY THE NEWEST FRAME
            # ------------------------------------------------

            in_rgb = queue.tryGet()

            if in_rgb is None:

                time.sleep(0.001)

                continue

            frame = in_rgb.getCvFrame()

            frame_number += 1

            with state_lock:
                total_frames += 1

            # ------------------------------------------------
            # RF-DETR
            #
            # Skip some frames to improve responsiveness.
            # The camera itself continues running.
            # ------------------------------------------------

            if frame_number % INFERENCE_EVERY_N_FRAMES == 0:

                try:

                    results = model.infer(
                        frame,
                        confidence=CONF_THRESHOLD
                    )

                    result = (
                        results[0]
                        if isinstance(results, list)
                        else results
                    )

                    detections = sv.Detections.from_inference(
                        result
                    )

                    # ------------------------------------------------
                    # BYTE TRACK
                    # ------------------------------------------------

                    detections = tracker.update_with_detections(
                        detections
                    )

                    last_detections = detections

                    with state_lock:
                        inference_frames += 1

                    # ------------------------------------------------
                    # EXTRACT DETECTION INFORMATION
                    # ------------------------------------------------

                    class_names_this_frame = list(
                        detections.data.get(
                            "class_name",
                            []
                        )
                    )

                    confidences = list(
                        detections.confidence
                    ) if detections.confidence is not None else []

                    tracker_ids = list(
                        detections.tracker_id
                    ) if detections.tracker_id is not None else []

                    last_detection_names = class_names_this_frame
                    last_detection_confidences = confidences
                    last_detection_ids = tracker_ids

                    # ------------------------------------------------
                    # CURRENT FRAME COUNTS
                    # ------------------------------------------------

                    frame_counts = defaultdict(int)

                    with state_lock:

                        for cname, tid in zip(
                            class_names_this_frame,
                            tracker_ids
                        ):

                            frame_counts[cname] += 1

                            if tid is not None:

                                seen_ids_by_class[cname].add(
                                    int(tid)
                                )

                        current_frame_counts.clear()

                        current_frame_counts.update(
                            frame_counts
                        )

                except Exception as e:

                    last_error = str(e)

                    print(
                        "Inference error:",
                        e
                    )

            # ------------------------------------------------
            # ANNOTATE EVERY CAMERA FRAME
            #
            # Even when RF-DETR is skipped, we display the
            # newest frame with the most recent detections.
            # ------------------------------------------------

            annotated = frame.copy()

            try:

                if len(last_detections) > 0:

                    annotated = box_annotator.annotate(
                        scene=annotated,
                        detections=last_detections
                    )

                    labels = []

                    for name, conf, tid in zip(
                        last_detection_names,
                        last_detection_confidences,
                        last_detection_ids
                    ):

                        labels.append(
                            f"#{tid} {name} {conf:.2f}"
                        )

                    annotated = label_annotator.annotate(
                        annotated,
                        detections=last_detections,
                        labels=labels
                    )

            except Exception as e:

                last_error = str(e)

            # ------------------------------------------------
            # DISPLAY COUNTS
            # ------------------------------------------------

            with state_lock:

                frame_counts_display = dict(
                    current_frame_counts
                )

                unique_counts_display = {
                    cname: len(
                        seen_ids_by_class[cname]
                    )
                    for cname in CLASS_NAMES
                }

                inference_count_display = inference_frames

                total_frame_display = total_frames

            # ------------------------------------------------
            # TEXT OVERLAY
            # ------------------------------------------------

            y = 25

            cv2.putText(
                annotated,
                "OAK-D + RF-DETR + ByteTrack",
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2
            )

            y += 25

            cv2.putText(
                annotated,
                f"Camera frames: {total_frame_display}",
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1
            )

            y += 22

            cv2.putText(
                annotated,
                f"RF-DETR frames: {inference_count_display}",
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1
            )

            y += 30

            cv2.putText(
                annotated,
                "Current:",
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 255),
                2
            )

            for cname in CLASS_NAMES:

                y += 22

                cv2.putText(
                    annotated,
                    f"{cname}: "
                    f"{frame_counts_display.get(cname, 0)}",
                    (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (0, 255, 255),
                    1
                )

            y += 30

            cv2.putText(
                annotated,
                "Unique seen:",
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2
            )

            for cname in CLASS_NAMES:

                y += 22

                cv2.putText(
                    annotated,
                    f"{cname}: "
                    f"{unique_counts_display[cname]}",
                    (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (0, 255, 0),
                    1
                )

            # ------------------------------------------------
            # ENCODE JPEG
            # ------------------------------------------------

            ok, jpg = cv2.imencode(
                ".jpg",
                annotated,
                [
                    cv2.IMWRITE_JPEG_QUALITY,
                    70
                ]
            )

            if ok:

                with state_lock:

                    latest_jpeg = jpg.tobytes()

    except Exception as e:

        last_error = str(e)

        print()
        print("========================================")
        print("CAMERA LOOP ERROR")
        print("========================================")
        print(e)

    finally:

        survey_end_time = time.time()

        print()
        print("Stopping OAK-D pipeline...")

        try:
            pipeline.stop()
        except Exception:
            pass

        print("OAK-D pipeline stopped.")


# ============================================================
# MJPEG STREAM
# ============================================================

def mjpeg_generator():

    while not stop_event.is_set():

        with state_lock:
            frame = latest_jpeg

        if frame is not None:

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame
                + b"\r\n"
            )

        # Don't make Flask itself consume unnecessary CPU.
        time.sleep(0.01)


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():

    return """
    <!DOCTYPE html>

    <html>

    <head>

        <title>OAK-D Survey Robot</title>

        <style>

            body {
                background: #111;
                color: white;
                font-family: Arial;
                text-align: center;
                margin: 0;
                padding: 20px;
            }

            img {
                width: 100%;
                max-width: 1000px;
                border: 2px solid white;
            }

        </style>

    </head>

    <body>

        <h1>OAK-D Survey Robot</h1>

        <img src="/video">

        <p>
            <a
                href="/counts"
                style="color:white;"
            >
                View Counts JSON
            </a>
        </p>

    </body>

    </html>
    """


# ============================================================
# VIDEO ROUTE
# ============================================================

@app.route("/video")
def video():

    return Response(
        mjpeg_generator(),
        mimetype=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        )
    )


# ============================================================
# COUNTS API
# ============================================================

@app.route("/counts")
def counts():

    with state_lock:

        return jsonify({

            "current_frame": dict(
                current_frame_counts
            ),

            "total_unique": {
                cname: len(
                    seen_ids_by_class[cname]
                )
                for cname in CLASS_NAMES
            },

            "camera_frames": total_frames,

            "rfdetr_frames": inference_frames,

            "error": last_error

        })


# ============================================================
# GENERATE OPENAI SUMMARY
# ============================================================

def generate_llm_summary(
    duration_minutes,
    unique_counts,
    total_frames_processed,
    inference_frames_processed
):

    print()
    print("========================================")
    print("GENERATING LLM SURVEY SUMMARY")
    print("========================================")

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:

        print("OPENAI_API_KEY is not set.")

        return

    # --------------------------------------------------------
    # Create OpenAI client
    # --------------------------------------------------------

    try:

        client = OpenAI(
            api_key=api_key,
            timeout=20.0
        )

    except Exception as e:

        print("Could not create OpenAI client:")
        print(e)

        return

    # --------------------------------------------------------
    # Survey information
    # --------------------------------------------------------

    survey_data = f"""
Survey duration:
{duration_minutes:.2f} minutes

Unique people detected:
{unique_counts.get("people", 0)}

Unique shrubs detected:
{unique_counts.get("shrubs", 0)}

Unique trees detected:
{unique_counts.get("trees", 0)}

Total camera frames:
{total_frames_processed}

RF-DETR inference frames:
{inference_frames_processed}
"""

    prompt = f"""
You are generating a concise environmental survey report
for an autonomous rover.

The rover used an OAK-D camera, RF-DETR object detection,
and ByteTrack tracking.

Survey data:

{survey_data}

Write a short survey summary for a project report.

Include:

1. Survey duration
2. Objects detected
3. Approximate environmental observations
4. A short overall assessment

Do not invent objects or measurements that are not present
in the supplied data.

Keep the report under 200 words.
"""

    # --------------------------------------------------------
    # Make ONE API request after the survey
    # --------------------------------------------------------

    try:

        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
            max_output_tokens=300
        )

        summary = response.output_text

        print()
        print("========================================")
        print("LLM SURVEY SUMMARY")
        print("========================================")
        print(summary)
        print("========================================")

        # Save summary locally
        try:

            with open(
                "survey_summary.txt",
                "w"
            ) as f:

                f.write(summary)

            print()
            print(
                "Summary saved to:"
                " survey_summary.txt"
            )

        except Exception as e:

            print(
                "Could not save summary:",
                e
            )

    except Exception as e:

        print()
        print("OpenAI request failed:")
        print(e)


# ============================================================
# PRINT FINAL SURVEY DATA
# ============================================================

def print_final_survey_data():

    global survey_end_time

    with state_lock:

        final_unique_counts = {
            cname: len(
                seen_ids_by_class[cname]
            )
            for cname in CLASS_NAMES
        }

        final_total_frames = total_frames

        final_inference_frames = inference_frames

    if survey_start_time is not None:

        end_time = (
            survey_end_time
            if survey_end_time is not None
            else time.time()
        )

        duration_minutes = (
            end_time - survey_start_time
        ) / 60.0

    else:

        duration_minutes = 0.0

    print()
    print("========================================")
    print("FINAL SURVEY DATA")
    print("========================================")

    print(
        f"Duration: "
        f"{duration_minutes:.2f} minutes"
    )

    print(
        f"Unique people: "
        f"{final_unique_counts['people']}"
    )

    print(
        f"Unique shrubs: "
        f"{final_unique_counts['shrubs']}"
    )

    print(
        f"Unique trees: "
        f"{final_unique_counts['trees']}"
    )

    print(
        f"Camera frames: "
        f"{final_total_frames}"
    )

    print(
        f"RF-DETR frames processed: "
        f"{final_inference_frames}"
    )

    print("========================================")

    return (
        duration_minutes,
        final_unique_counts,
        final_total_frames,
        final_inference_frames
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("========================================")
    print("AUTONOMOUS ENVIRONMENTAL SURVEY")
    print("OAK-D + RF-DETR + BYTETRACK + OPENAI")
    print("========================================")

    # --------------------------------------------------------
    # Start camera worker
    # --------------------------------------------------------

    worker = threading.Thread(
        target=camera_worker,
        daemon=True
    )

    worker.start()

    # --------------------------------------------------------
    # Start Flask
    # --------------------------------------------------------

    try:

        app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            threaded=True,
            use_reloader=False
        )

    except KeyboardInterrupt:

        print()
        print("CTRL+C received.")

    finally:

        # ----------------------------------------------------
        # Stop camera
        # ----------------------------------------------------

        stop_event.set()

        print(
            "Waiting for camera thread to stop..."
        )

        worker.join(timeout=5)

        # ----------------------------------------------------
        # Final survey data
        # ----------------------------------------------------

        (
            duration_minutes,
            unique_counts,
            final_total_frames,
            final_inference_frames
        ) = print_final_survey_data()

        # ----------------------------------------------------
        # OpenAI summary ONLY AFTER SURVEY
        # ----------------------------------------------------

        generate_llm_summary(
            duration_minutes,
            unique_counts,
            final_total_frames,
            final_inference_frames
        )

        print()
        print("========================================")
        print("PROGRAM FINISHED")
        print("========================================")
