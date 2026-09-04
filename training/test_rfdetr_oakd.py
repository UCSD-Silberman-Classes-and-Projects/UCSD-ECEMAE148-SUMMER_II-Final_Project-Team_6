import os
import cv2
import depthai as dai
import supervision as sv

from collections import Counter
from inference import get_model


# ============================================================
# ROBOFLOW SETTINGS
# ============================================================

# The Roboflow key is per-account and is NOT in this repo. Put it in
# ~/.survey_keys (chmod 600) and source it:
#   export ROBOFLOW_API_KEY=your-key
ROBOFLOW_API_KEY = os.environ.get("ROBOFLOW_API_KEY", "")

MODEL_ID = "krishna-visanakarrala/mae-148-project-model-1-rfdetr-small-t1"


# ============================================================
# LOAD MODEL
# ============================================================

print("========================================")
print("Loading RF-DETR model...")
print("========================================")

model = get_model(
    model_id=MODEL_ID,
    api_key=ROBOFLOW_API_KEY
)

print("RF-DETR model loaded!")


# ============================================================
# CONNECT TO OAK-D
# ============================================================

print()
print("========================================")
print("Connecting to OAK-D...")
print("========================================")

devices = dai.Device.getAllAvailableDevices()

if not devices:
    print("ERROR: No OAK-D found.")
    exit()

device_info = devices[0]

print("Device found:")
print(device_info)

device = dai.Device(device_info)

print("OAK-D connected successfully!")
print("Device:", device.getDeviceName())


# ============================================================
# CREATE PIPELINE
# ============================================================

print()
print("Creating OAK-D pipeline...")

pipeline = dai.Pipeline()

camera = pipeline.create(dai.node.Camera)

video = camera.build(
    dai.CameraBoardSocket.CAM_A
).requestOutput(
    (1280, 720),
    dai.ImgFrame.Type.BGR888p
)

queue = video.createOutputQueue()

pipeline.start(device)

print("Camera pipeline started!")

print()
print("========================================")
print("OAK-D + RF-DETR")
print("Press Q to quit")
print("========================================")


# ============================================================
# ANNOTATORS
# ============================================================

box_annotator = sv.BoxAnnotator()
label_annotator = sv.LabelAnnotator()


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    frame = queue.get().getCvFrame()

    # Run RF-DETR
    results = model.infer(
        frame,
        confidence=0.35
    )

    result = results[0]

    # Convert to Supervision
    detections = sv.Detections.from_inference(result)

    # Draw boxes
    annotated = box_annotator.annotate(
        scene=frame.copy(),
        detections=detections
    )

    # Create labels
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

    # ========================================================
    # COUNT OBJECTS
    # ========================================================

    counts = Counter()

    for prediction in result.predictions:

        class_name = prediction.class_name.lower()

        counts[class_name] += 1

    # ========================================================
    # DISPLAY COUNTS
    # ========================================================

    cv2.rectangle(
        annotated,
        (10, 10),
        (320, 125),
        (0, 0, 0),
        -1
    )

    cv2.putText(
        annotated,
        f"TREES: {counts.get('trees', 0)}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2
    )

    cv2.putText(
        annotated,
        f"SHRUBS: {counts.get('shrubs', 0)}",
        (20, 75),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2
    )

    cv2.putText(
        annotated,
        f"PEOPLE: {counts.get('people', 0)}",
        (20, 110),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2
    )

    # Display
    cv2.imshow(
        "OAK-D + RF-DETR",
        annotated
    )

    # Quit
    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break


# ============================================================
# CLEANUP
# ============================================================

cv2.destroyAllWindows()
device.close()

print()
print("Camera stopped.")

