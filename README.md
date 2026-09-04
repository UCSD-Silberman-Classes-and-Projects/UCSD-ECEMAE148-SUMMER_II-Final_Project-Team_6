# <div align="center">Grove: An Autonomous Environmental Survey Robot with LLM-based Reporting</div>
### <div align="center">MAE 148 / ECE 148 Final Project</div>
#### <div align="center">Team 6 — Summer Session II 2026</div>

<div align="center">
  <img src="docs/media/media_frame3.jpg" width="700">
</div>

## Team Members

**Chuck Davies** — Department of Electrical and Computer Engineering

**Farbod Haeri** — Department of Mechanical and Aerospace Engineering

**Krishna Visanakarrala** — Department of Electrical and Computer Engineering

Calli Hill, Department of Natural Resources Management and Environmental Sciences, CPSLO


<hr>

## Abstract

Grove is an autonomous car that surveys its environment. It drives a saved GPS path on its own, detects trees, shrubs and people from an OAK-D camera while driving, ties every detection to a centimetre-accurate RTK GPS position, and produces both a numeric report and an LLM-written summary the moment the lap ends.

The detection runs **on the car**, on a Hailo-8 AI HAT+, not on a laptop and not in the cloud. We trained our own three-class YOLOv8 model by knowledge distillation, compiled it to the Hailo's `.hef` format, and measured it on real hardware at **mAP50 0.735 and 41.8 fps** — roughly 40× the throughput of the same model on the Raspberry Pi's CPU. 

<hr>

## What We Promised

### Must Have
* Autonomous GPS-guided navigation along a predefined route
* Environmental scanning using the OAK-D camera
* Object detection, classification, and geolocation logging
* Automated LLM-based report generation 

### Nice to Have
* Map generation of the surveyed area
* Anomaly detection in environmental data
* Autonomous exploration without a predefined route
* Expanded environmental metrics (e.g., tree species, trunk diameter)

<hr>

## Accomplishments

- **Full autonomous survey lap**, started from a browser button: `LAP 1 COMPLETE — 114.7 m driven, back within 5.0 m`
- **Detection on the Hailo-8 AI HAT+.** Our own 3-class model, compiled to `.hef`, verified on hardware at **mAP50 0.735 / 41.8 fps** against the 261-image validation set
- **Knowledge distillation**: an RF-DETR teacher labelled our footage, and we trained a YOLOv8s student on it — no manual labelling of the survey set
- **RTK-fixed positioning** throughout the lap (401 of 441 parsed GGA fixes were RTK-FIXED)
- **Live dashboard** — camera feed with detection boxes, counts, and an LLM narrating every 30 s while the car drives
- **Reports** — numeric, LLM-written, plus an annual carbon estimate
- **Single-file offline report** ([`results/Grove_lap_20260903.html`](results/Grove_lap_20260903.html)) with the whole lap embedded: scrubbable annotated video, route map, and every report 

### Results from the final lap

| | |
|---|---|
| Distance | 114.7 m, lap closed within 5.0 m |
| Frames | 356 recorded, all 356 annotated on the HAT |
| Raw detections | 9,227 |
| **Distinct objects** | **25 trees · 26 shrubs · 7 people** |
| Positioning | RTK-FIXED dominant |
| Carbon (illustrative) | −1,932 kg CO₂/yr |

<div align="center">
  <img src="docs/media/media_map.svg" width="460"><br>
  <em>The driven loop. Each dot is a merged detection cluster.</em>
</div>

<hr>

## How It Works

```
OAK-D camera ──► record_survey.py ──► frames/*.jpg ─┐
                                                    ├─► live_survey.py ──► Hailo-8 HAT
RTK GPS ──► p1_runner ──► survey_gps_logger.py ─────┘         │            (grove_lb.hef)
                                                              ▼
                                             detections + GPS ──► cluster within 3 m
                                                              ▼
                                        survey report · LLM report · carbon · live narration
                                                              ▼
                                    survey_web.py  (dashboard)   make_offline.py  (shareable HTML)
```

**Why the counts are clusters, not objects.** We have no depth. A detection is
tagged with the position of *the car* when it fired, so we merge detections
within 3 m into one "distinct object". Two trees passed within that radius
become one. Every report says so, and the counts are a **lower bound**.

<hr>

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 5 | main compute |
| **Hailo-8 AI HAT+** (26 TOPS) | runs the detection model |
| OAK-D | camera (used as a plain RGB camera here) |
| Quectel LG69T + Point One Polaris | RTK GNSS, centimetre fixes |
| VESC | motor controller |
| Logitech F710 | manual override |

<hr>

## Software

| Directory | What's in it |
|---|---|
| [`survey/`](survey) | everything that runs on the car — orchestration, detection, GPS, dashboard |
| [`model/`](model) | the compiled `grove_lb.hef`, its class list, and the Hailo compile script |
| [`offline_report/`](offline_report) | builds the single-file shareable HTML, plus the accuracy tooling |
| [`results/`](results) | the final lap's reports and its offline HTML report |
| [`docs/`](docs) | reproduction guide and hardware notes |

### The main programs we wrote

| File | Role |
|---|---|
| `survey/survey.sh` | one command runs the whole survey: RTK → drive loop → camera → detection → auto-drive → auto-stop → report |
| `survey/live_survey.py` | detects on frames as they land, clusters by GPS, narrates, writes the report |
| `survey/analyze_survey.py` | clustering, carbon and positioning maths, offline analysis, LLM report |
| `survey/hailo_backend.py` | the Hailo-8 detector, drop-in compatible with the CPU detector |
| `survey/survey_web.py` | the dashboard — live feed, rig control, saved laps |
| `survey/lap_watch.py` | decides when a lap has actually closed, and stops the car |
| `survey/set_drive_mode.py` | puts the car into full autonomy from software |
| `offline_report/make_offline.py` | builds the self-contained HTML report |
| `model/compile_hef.py` | ONNX → Hailo `.hef`, the step that took the longest to get right |

<hr>

## Quickstart

On the car:

```bash
./survey/survey.sh --live --auto        # drives itself, detects, stops, reports
./survey/survey.sh --check              # preflight only, moves nothing
```

From a browser on the same network — `http://<pi-ip>:8090` — press **Start lap**.

Build a shareable report for a finished lap:

```bash
./offline_report/make_lap_report.sh 20260903_165910
```

Full setup, including the RTK credentials this repo deliberately does not
contain, is in the [reproduction guide](docs/reproduction.md).

<hr>

## Challenges

**Person detection limitations** The OAK-D captured insufficient real-world person images due to low campus foot traffic; we supplemented with external datasets (mostly walking poses). More varied examples (e.g., sitting) would have improved robustness.

**GPS Hardware Issues** Our hardware for the GPS was not working, which meant we had to spend a lot of time debugging. We had to borrow another group's hardware components to get our GPS laps to work. 

**Performance Limitations on the Pi** Running the full stack on the Raspberry Pi caused GPS drift and path deviation due to processing delays. We offloaded detection tasks to the Hailo-8 AI HAT+ to distribute compute load and maintain real-time performance.

<hr>

## Final Project Media

### Interactive lap report
Download [`results/Grove_lap_20260903.html`](results/Grove_lap_20260903.html) and
open it — the full annotated video, route map and every report, in one file that
works with the internet off.

<div align="center">
  <img src="docs/media/media_frame1.jpg" width="380">
  <img src="docs/media/media_frame2.jpg" width="380">
</div>

### Final Presentation Slides
[View slides on Google Slides](https://docs.google.com/presentation/d/1sO4k4Od_j3QZOskuEYOf58iFhr3uQPs6sYA8-_jKNcs/edit?usp=sharing)

### Demo Video
[Watch on YouTube](https://youtu.be/8qFMYO2c-sA)

<hr>

## Documentation

- [Reproduction guide](docs/reproduction.md) — set the car up and run a survey from scratch
- [Hardware notes](docs/hardware.md) — wiring, ports, and the traps that cost us time

<hr>

## Acknowledgements

Thank you to Professor Jack Silberman and TAs Jose Castillo-Valdovinos and
Daniel Galicia Ortiz for the course.

Additionally, thank you to Calli Hill for helping us with this project and providing many great ideas! 


<hr>

## Contacts

* Chuck Davies — Electrical and Computer Engineering

* Farbod Haeri — Mechanical and Aerospace Engineering | farbodh97@gmail.com | [GitHub](https://github.com/Farbod97)

* Krishna Visanakarrala — Electrical and Computer Engineering 
