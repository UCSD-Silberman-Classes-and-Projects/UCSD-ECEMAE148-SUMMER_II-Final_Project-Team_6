# Reproduction guide

Everything here was run on a Raspberry Pi 5 with a Hailo-8 AI HAT+, an OAK-D
camera, a Quectel LG69T RTK receiver and a VESC, under DonkeyCar 5.3.

## 1. Credentials (not in this repo)

RTK corrections and the LLM reports both need keys. Put them in
`~/.survey_keys`, `chmod 600`, and nothing else:

```bash
export P1_DEVICE_ID=your-point-one-device-id
export P1_POLARIS_KEY=your-polaris-key
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-5.6-luna
```

`start_survey.sh` and `rtk_watchdog.sh` source this file and refuse to run
without the RTK values. The LLM steps degrade quietly if `OPENAI_API_KEY` is
missing: you still get the numeric report.

## 2. Serial ports

The GNSS receiver is **one dual-port USB chip**, and the two halves are not
interchangeable:

| Port | Who owns it |
|---|---|
| `...-if00-port0` | the correction runner (`p1_runner`) |
| `...-if01-port0` | DonkeyCar, reading NMEA |

Always address them through `/dev/serial/by-id/...`, never `ttyUSB0`/`ttyUSB1` —
those swap across reboots.

## 3. Which Python runs what

This matters more than it looks.

| Interpreter | numpy | Use it for |
|---|---|---|
| system `python3` | 1.24.2 | **anything touching the Hailo HAT** |
| `~/obj-detection-env` | 2.2.6 | the CPU detector fallback |
| `~/env` | 2.2.6 | DonkeyCar, the dashboard |

`pyhailort` is a C extension built against **numpy 1.x**. Under numpy 2 every
`infer()` fails with `Memory size of vstream ... does not match the frame count
(got 0)` and no array form works around it. `survey.sh` picks the interpreter
automatically: HAT present → system `python3` with `PYTHONPATH=~/hatlibs`
(an isolated `pip install --target` of `openai==1.109.1`), otherwise the CPU env.

## 4. Record a path

Drive the loop manually once and save it. DonkeyCar's path-follow template
writes `donkey_path.csv`, and the drive loop **auto-loads it at startup** — the
controller's X button only reloads it.

## 5. Run a survey

```bash
./survey/survey.sh --check              # preflight, moves nothing
./survey/survey.sh --live --auto        # the real thing
```

Useful flags: `--fps`, `--laps`, `--radius` (merge radius, m), `--conf`
(detection floor, default 0.25), `--manual` (no auto-stop), `--no-report`.

Or open `http://<pi-ip>:8090` and press **Start lap**.

## 6. Rebuild the model

`model/compile_hef.py` takes `best.onnx` (YOLOv8s, 3 classes) to a Hailo `.hef`.
Run it inside the Hailo Dataflow Compiler **3.30.0** environment, which pairs
with the Pi's HailoRT 4.20.0. Two things that are easy to get wrong:

- **Cut the graph before the detection head.** YOLOv8's head cannot be parsed
  (`/model.22/dfl/Reshape`, `/Sub`, `/Add_1` are unsupported). Pass the six
  detection convolutions as `end_node_names` and let the chip's NMS decode.
- **Calibrate with letterboxed frames**, and letterbox at inference too. Plain
  resize measured mAP50 0.618 against letterbox's 0.795 on the same weights.

Deploy with `offline_report/install_hat_model.sh path/to/grove_lb.hef` — it
verifies the HEF magic bytes, checksums the file on the Pi, and rolls back on a
mismatch.

## 7. Check accuracy yourself

```bash
CONF=0.2 python3 offline_report/hat_reference.py    # on the Pi, over the val split
python3 offline_report/score_vs_gt.py hat_reference.json labels/   # AP50 per class
```

Our numbers on the 261-image validation set, on-chip: trees 0.842, shrubs 0.793,
people 0.569, **mAP50 0.735** at 41.8 fps.

## 8. Build a shareable report

```bash
./offline_report/make_lap_report.sh <run_id>
```

Writes one self-contained HTML file — frames, reports, narration and route map
all embedded, no network needed.
