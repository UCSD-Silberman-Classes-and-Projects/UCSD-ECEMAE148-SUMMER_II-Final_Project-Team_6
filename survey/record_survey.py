#!/usr/bin/env python3
"""
record_survey.py  --  MAE 148 Team 6

Records the survey lap. Saves timestamped JPEG frames and NOTHING ELSE.
No inference runs here, so the CPU stays free and the car drives straight.

Why this exists:
    RF-DETR needs 861 ms per frame on this Pi's CPU. Running it during the
    lap took 352% CPU (all four cores, 0% idle, 79 C), starved the drive
    loop, and the car left the route. A survey does not need real-time
    detection: record now, detect afterwards at full speed on every frame.

Usage:
    ./record_survey.py                 # 4 fps, default output dir
    ./record_survey.py --fps 2         # lighter on disk
    ./record_survey.py --tag lap1      # label this run

Stop with Ctrl+C. Frames land in data/frames/, the index in data/logs/.
Pair each run with gps_logger on the DonkeyCar side; analyze_survey.py
joins the two by timestamp.
"""

import argparse
import csv
import os
import shutil
import signal
import sys
import time

import cv2
import depthai as dai

# Matches test_rfdetr_web2.py so recorded frames look exactly like the
# frames the model was tested on.
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 360
CAMERA_FPS = 30
QUEUE_SIZE = 1

JPEG_QUALITY = 85
MIN_FREE_MB = 500          # refuse to start if the card is nearly full

stop_requested = False


def handle_sigint(signum, frame):
    global stop_requested
    stop_requested = True
    print("\n[record] stop requested, flushing...", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=float, default=4.0,
                    help="frames saved per second (default 4)")
    ap.add_argument("--tag", default="",
                    help="optional label for this run")
    ap.add_argument("--outdir", default=os.path.expanduser("~/oakd_project/data"))
    args = ap.parse_args()

    run_id = time.strftime("%Y%m%d_%H%M%S")
    if args.tag:
        run_id += "_" + args.tag

    frames_dir = os.path.join(args.outdir, "frames", run_id)
    logs_dir = os.path.join(args.outdir, "logs")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)

    free_mb = shutil.disk_usage(args.outdir).free / (1024 * 1024)
    if free_mb < MIN_FREE_MB:
        print(f"[record] ABORT: only {free_mb:.0f} MB free, need {MIN_FREE_MB}")
        return 1

    index_path = os.path.join(logs_dir, f"frames_{run_id}.csv")

    print("=" * 52)
    print("SURVEY RECORDER  (no inference - CPU stays free)")
    print("=" * 52)
    print(f"  run id     : {run_id}")
    print(f"  save rate  : {args.fps:g} fps")
    print(f"  frames  -> : {frames_dir}")
    print(f"  index   -> : {index_path}")
    print(f"  free space : {free_mb:.0f} MB")
    print()

    try:
        pipeline = dai.Pipeline()
        camera = pipeline.create(dai.node.Camera)
        video = camera.build(
            dai.CameraBoardSocket.CAM_A
        ).requestOutput(
            (CAMERA_WIDTH, CAMERA_HEIGHT),
            dai.ImgFrame.Type.BGR888p
        )
        queue = video.createOutputQueue(maxSize=QUEUE_SIZE, blocking=False)
    except Exception as e:
        print("[record] PIPELINE ERROR:", e)
        return 1

    try:
        pipeline.start()          # DepthAI 3.9 style, same as their app
    except Exception as e:
        print("[record] OAK-D ERROR:", e)
        return 1

    print("[record] camera live. Recording. Press Ctrl+C to stop.\n")

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    save_interval = 1.0 / args.fps if args.fps > 0 else 0.0
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]

    saved = 0
    seen = 0
    t_start = time.time()
    next_save = t_start
    last_report = t_start

    index_file = open(index_path, "w", newline="")
    writer = csv.writer(index_file)
    writer.writerow(["frame_id", "filename", "unix_time", "iso_time"])

    try:
        while not stop_requested:
            packet = queue.tryGet()
            if packet is None:
                time.sleep(0.001)
                continue

            seen += 1
            now = time.time()
            if now < next_save:
                continue                      # drop, keeps disk + CPU low
            next_save = now + save_interval

            frame = packet.getCvFrame()
            fname = f"f_{saved:06d}.jpg"
            cv2.imwrite(os.path.join(frames_dir, fname), frame, encode_params)

            writer.writerow([saved, fname, f"{now:.3f}",
                             time.strftime("%Y-%m-%dT%H:%M:%S",
                                           time.localtime(now))])
            saved += 1

            if saved % 20 == 0:
                index_file.flush()

            if now - last_report >= 5.0:
                el = now - t_start
                print(f"  [{el:6.1f}s] saved {saved:5d} frames "
                      f"({saved/el:.1f}/s) | camera {seen/el:.1f} fps",
                      flush=True)
                last_report = now
    finally:
        index_file.flush()
        index_file.close()
        try:
            pipeline.stop()
        except Exception:
            pass

    elapsed = max(time.time() - t_start, 1e-6)
    size_mb = sum(
        os.path.getsize(os.path.join(frames_dir, f))
        for f in os.listdir(frames_dir)
    ) / (1024 * 1024)

    print()
    print("=" * 52)
    print("RECORDING COMPLETE")
    print("=" * 52)
    print(f"  duration     : {elapsed:.1f} s")
    print(f"  frames saved : {saved}  ({saved/elapsed:.2f}/s)")
    print(f"  camera saw   : {seen}  ({seen/elapsed:.1f} fps)")
    print(f"  disk used    : {size_mb:.1f} MB")
    print(f"  run id       : {run_id}")
    print()
    print("  Next: ./analyze_survey.py --run " + run_id)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
