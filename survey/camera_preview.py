#!/usr/bin/env python3
"""
camera_preview.py  --  see through the camera BEFORE a run  (Claude, 2026-09-02)

Opens the OAK-D and writes each frame to a single JPEG the dashboard streams.

THE OAK-D IS SINGLE-OWNER. While this holds the camera, record_survey.py
cannot open it. survey_web.py therefore stops this process and waits for the
device to re-enumerate before starting a lap - a preview left running would
make the run fail with "No DepthAI device found!".
"""
import os
import signal
import sys
import time

OUT = os.path.expanduser("~/oakd_project/data/live/preview.jpg")
# Hard ceiling. The OAK-D is single-owner: a preview that outlives its usefulness
# silently blocks every future run with "No DepthAI device found!". It expires on
# its own even if nothing ever tells it to stop.
MAX_SECONDS = float(os.environ.get("PREVIEW_MAX_SECONDS", 900))
STOP = False


def _stop(*_a):
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    import cv2
    import depthai as dai

    # depthai 3.x API, matching record_survey.py exactly so the preview and
    # the recorder behave identically against the same device.
    pipeline = dai.Pipeline()
    camera = pipeline.create(dai.node.Camera)
    video = camera.build(
        dai.CameraBoardSocket.CAM_A
    ).requestOutput((640, 360), dai.ImgFrame.Type.BGR888p)
    queue = video.createOutputQueue(maxSize=2, blocking=False)

    print("opening the OAK-D ...", flush=True)
    pipeline.start()
    print("preview live -> %s" % OUT, flush=True)

    n = 0
    t_end = time.time() + MAX_SECONDS
    try:
        while not STOP and time.time() < t_end:
            packet = queue.tryGet()
            if packet is None:
                time.sleep(0.02)
                continue
            frame = packet.getCvFrame()
            # cv2.imwrite picks the encoder from the EXTENSION, so the temp
            # file must still end in .jpg - "preview.jpg.tmp" fails.
            tmp = OUT.replace(".jpg", ".tmp.jpg")
            cv2.imwrite(tmp, frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
            os.replace(tmp, OUT)
            n += 1
    finally:
        try:
            pipeline.stop()
        except Exception:
            pass

    try:
        os.remove(OUT)
    except OSError:
        pass
    print("preview stopped after %d frames; camera released" % n, flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("preview failed: %s: %s" % (type(e).__name__, e), flush=True)
        sys.exit(1)
