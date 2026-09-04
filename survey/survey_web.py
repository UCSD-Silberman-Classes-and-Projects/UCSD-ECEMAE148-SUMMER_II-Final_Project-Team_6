#!/usr/bin/env python3
"""
survey_web.py  --  MAE 148 Team 6 survey results dashboard

Serves the finished survey: the LLM write-up, the numeric report, a scrubber
through the recorded frames, and a map of the GPS track with detections.

Fully self-contained: no CDN, no external fonts, no pip installs. The Pi has
no internet, so anything external would simply fail to load.

    ~/survey_web.py            -> http://<pi>:8090
    ~/survey_web.py 9000       -> another port
"""
import csv
import subprocess
import time
import glob
import html
import json
import math
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# SURVEY_ROOT points at a synced copy of the vehicle's data, so this same
# server runs on a laptop with the robot switched off or out of range.
# Unset (the normal case) it reads the live paths on the car.
_ROOT = os.environ.get("SURVEY_ROOT")
OFFLINE = bool(_ROOT)
if OFFLINE:
    _ROOT = os.path.expanduser(_ROOT)
    DATA = os.path.join(_ROOT, "data")
    REPORTS = os.path.join(_ROOT, "reports")
    GPSLOGS = os.path.join(_ROOT, "gpslogs")
else:
    DATA = os.path.expanduser("~/oakd_project/data")
    REPORTS = os.path.expanduser("~/oakd_project/reports")
    GPSLOGS = os.path.expanduser("~/gpscar/logs")
FRAMES = os.path.join(DATA, "frames")
LOGS = os.path.join(DATA, "logs")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090

CLASS_SLOT = {"trees": 1, "shrubs": 2, "people": 3}

# Population averages per object per year. MUST match analyze_survey.py -
# the reports and this page have to agree. Not measurements of these plants.
CO2_KG_PER_YEAR = {"trees": 21.8, "shrubs": 3.0, "people": 365.0}
CO2_ABSORBS = ("trees", "shrubs")


def carbon(counts):
    """counts: {class: {"distinct": n, ...}} -> absorbed/emitted/net kg per year."""
    absorbed = emitted = 0.0
    for cls, v in (counts or {}).items():
        rate = CO2_KG_PER_YEAR.get(cls)
        n = v.get("distinct") if isinstance(v, dict) else v
        if not rate or not n:
            continue
        if cls in CO2_ABSORBS:
            absorbed += n * rate
        else:
            emitted += n * rate
    if not (absorbed or emitted):
        return None
    return {"absorbed": round(absorbed), "emitted": round(emitted),
            "net": round(absorbed - emitted)}


def run_ids():
    out = []
    for d in sorted(glob.glob(os.path.join(FRAMES, "*")), reverse=True):
        if os.path.isdir(d):
            out.append(os.path.basename(d))
    return out


def frame_list(run):
    """Frames that actually contain an image.

    The 31 August power losses left 146 zero-byte JPEGs (115 in 20260831_191107,
    31 in 20260831_194925) where the recorder created the file but never wrote
    it. Serving those returns an empty 200 and the player shows a broken image,
    so they are skipped: the scrubber then offers only frames that will display.
    """
    out = []
    for p in sorted(glob.glob(os.path.join(FRAMES, run, "f_*.jpg"))):
        try:
            if os.path.getsize(p) > 0:
                out.append(p)
        except OSError:
            continue
    return out


def read_text(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def parse_counts(survey_text):
    """Pull 'trees  25 distinct  (614 raw detections)' out of the report."""
    counts = {}
    for m in re.finditer(r"^\s+(\w+)\s+(\d+)\s+distinct\s+\((\d+)\s+raw",
                         survey_text, re.M):
        counts[m.group(1)] = {"distinct": int(m.group(2)),
                              "raw": int(m.group(3))}
    return counts


def indexed_frame_count(run):
    """How many frames the recorder actually indexed for this run."""
    path = os.path.join(LOGS, "frames_%s.csv" % run)
    try:
        with open(path) as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except OSError:
        return 0


def parse_coverage(survey_text, run):
    """Reports written before the honesty fix hardcode '100% of recorded
    frames' even when subsampled. Never trust that claim - cross-check it
    against the frame index and report what the data actually supports."""
    recorded = indexed_frame_count(run)
    m = re.search(r"Frames analysed\s*:\s*(\d+) of (\d+) recorded\s+\((\d+)%,"
                  r"\s*every (\d+)", survey_text)
    if m:
        return {"analysed": int(m.group(1)), "recorded": int(m.group(2)),
                "pct": int(m.group(3)), "every": int(m.group(4)),
                "stale": False}
    m = re.search(r"Frames analysed\s*:\s*(\d+)", survey_text)
    if not m:
        return None
    n = int(m.group(1))
    if recorded and n < recorded:
        # old report claimed 100%; the index says otherwise
        return {"analysed": n, "recorded": recorded,
                "pct": round(100.0 * n / recorded), "every": None,
                "stale": True}
    return {"analysed": n, "recorded": recorded or n, "pct": 100,
            "every": 1, "stale": False}


def load_detections(run, cap=1500):
    """Detection points that carry a GPS fix, for the map."""
    path = os.path.join(LOGS, "detections_%s.csv" % run)
    pts = []
    try:
        with open(path) as fh:
            for r in csv.DictReader(fh):
                try:
                    pts.append([float(r["utm_x"]), float(r["utm_y"]),
                                r["class"]])
                except (TypeError, ValueError):
                    continue
    except OSError:
        return []
    if len(pts) > cap:
        step = len(pts) // cap + 1
        pts = pts[::step]
    return pts


def load_track(run, cap=800):
    """The car's path. GPS logs are named by their own clock, not the run,
    so take the log whose time span overlaps this run's frames."""
    idx = os.path.join(LOGS, "frames_%s.csv" % run)
    lo = hi = None
    try:
        with open(idx) as fh:
            ts = []
            for r in csv.DictReader(fh):
                try:
                    ts.append(float(r["unix_time"]))
                except (TypeError, ValueError):
                    continue
            if ts:
                lo, hi = min(ts), max(ts)
    except OSError:
        pass
    # Without a frame time window there is nothing to match against, and
    # guessing would paint another run's path onto this one's map.
    if lo is None:
        return []
    best = []
    for p in sorted(glob.glob(os.path.join(GPSLOGS, "gps_*.csv"))):
        pts = []
        try:
            with open(p) as fh:
                for r in csv.DictReader(fh):
                    try:
                        t = float(r["unix_time"])
                        x = float(r["pos_x"])
                        y = float(r["pos_y"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    # keep ONLY samples inside this run's window: the GPS
                    # logger runs independently, so a log can span several
                    # runs or none of them.
                    if lo - 5 <= t <= hi + 5:
                        pts.append([x, y])
        except OSError:
            continue
        if len(pts) > len(best):
            best = pts
    if len(best) > cap:
        step = len(best) // cap + 1
        best = best[::step]
    return best


def run_summary(run):
    """Enough to tell the runs apart in the picker. The whole reason this
    exists: five runs recorded on one evening look identical by name, and
    only the ones driven under manage.py carry a GPS track."""
    span = ""
    n = 0
    idx = os.path.join(LOGS, "frames_%s.csv" % run)
    try:
        ts = []
        with open(idx) as fh:
            for r in csv.DictReader(fh):
                try:
                    ts.append(float(r["unix_time"]))
                except (TypeError, ValueError):
                    continue
        if ts:
            n = len(ts)
            span = "%s-%s" % (time.strftime("%H:%M", time.localtime(min(ts))),
                              time.strftime("%H:%M", time.localtime(max(ts))))
    except OSError:
        pass
    if not n:
        n = len(frame_list(run))
    return {"id": run, "n_frames": n, "span": span,
            "gps": len(load_track(run, cap=10)) > 0,
            "n_annot": len(annot_list(run)),
            "report": bool(read_text(os.path.join(REPORTS,
                                                  "survey_%s.txt" % run)))}


SURVEY_SH = os.path.expanduser("~/survey.sh")
RUNLOG = os.path.expanduser("~/survey_run.log")
LOOP_M = 107.9


def proc_running(needle):
    """Is a process whose command line contains `needle` alive?

    Deliberately NOT pgrep -f: that matches its own command line, which has
    already cost this project two debugging sessions (it killed a parent shell
    and it faked an 'already running' guard). Reading /proc and skipping our
    own pid cannot do that.
    """
    me = str(os.getpid())
    try:
        pids = os.listdir("/proc")
    except OSError:
        return False
    for pid in pids:
        if not pid.isdigit() or pid == me:
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf8", "replace")
        except OSError:
            continue
        if needle in cmd:
            return True
    return False


def live_gps_log(fresh=90):
    """The GPS log being appended to right now, or None."""
    logs = sorted(glob.glob(os.path.join(GPSLOGS, "gps_*.csv")),
                  key=os.path.getmtime)
    if not logs:
        return None
    p = logs[-1]
    if time.time() - os.path.getmtime(p) > fresh:
        return None
    return p


def lap_progress():
    """Same anchor rule lap_watch.py uses, so the page agrees with the watcher."""
    p = live_gps_log()
    if not p:
        return None
    pts = []
    try:
        with open(p) as fh:
            for r in csv.DictReader(fh):
                try:
                    pts.append((float(r["pos_x"]), float(r["pos_y"])))
                except (TypeError, ValueError, KeyError):
                    continue
    except OSError:
        return None
    need = LOOP_M * 0.75
    anchor = None
    trav = 0.0
    armed = False
    laps = 0
    for i in range(1, len(pts)):
        step = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        if step > 5.0:
            continue
        trav += step
        if anchor is None and trav >= 2.0:
            anchor = pts[i]
            trav = 0.0
            continue
        if anchor is None:
            continue
        dh = math.hypot(pts[i][0] - anchor[0], pts[i][1] - anchor[1])
        if not armed and dh > 10.0:
            armed = True
        if armed and trav >= need and dh <= 5.0:
            laps += 1
            anchor = pts[i]
            trav = 0.0
            armed = False
    if not pts:
        return None
    dh = (math.hypot(pts[-1][0] - anchor[0], pts[-1][1] - anchor[1])
          if anchor else None)
    return {"laps": laps, "driven": round(trav, 1), "need": round(need, 1),
            "from_start": round(dh, 1) if dh is not None else None,
            "left_start": armed, "samples": len(pts),
            "pct": min(100, round(100.0 * trav / need)) if need else 0}


REPORTLOG = os.path.expanduser("~/last_report.log")
_PROG_RE = re.compile(r"(\d+)/(\d+) frames \| ([\d.]+) fps \| (\d+) detections"
                      r" \| eta (\d+)s")


def analysis_progress():
    """Where the offline analysis has got to, parsed from its own log.

    The analyser prints a progress line every 25 frames; the LLM call happens
    after the last one, and used to look like a hang because nothing on the
    page changed for the ~30 s it takes.
    """
    try:
        with open(REPORTLOG) as fh:
            txt = fh.read()
    except OSError:
        return None
    if not txt:
        return None
    run = None
    # the analyser writes "  run        : X"; report_survey.sh writes
    # "Analysing run: X" to a different log. Accept either.
    m = re.search(r"(?:Analysing run:|^\s*run\s*:)\s*(\S+)", txt, re.M)
    if m:
        run = m.group(1)
    last = None
    for last in _PROG_RE.finditer(txt):
        pass
    done = total = dets = eta = None
    fps = None
    if last:
        done, total = int(last.group(1)), int(last.group(2))
        fps = float(last.group(3))
        dets = int(last.group(4))
        eta = int(last.group(5))
    if "LLM SURVEY REPORT" in txt or "Saved -> " in txt and "llm_report" in txt:
        phase = "done"
    elif "SURVEY RESULTS" in txt:
        phase = "writing"          # detection finished, LLM call in flight
    elif last:
        phase = "detecting"
    elif "loading model" in txt:
        phase = "loading"
    else:
        phase = "starting"
    pct = int(100.0 * done / total) if (done and total) else 0
    return {"run": run, "phase": phase, "done": done, "total": total,
            "dets": dets, "eta": eta, "fps": fps, "pct": pct}


LIVEDIR = os.path.join(DATA, "live")


def live_state():
    """Progress written by live_survey.py while the car is driving.

    Only the newest file matters, and only if it was touched recently: a
    finished run's file lingers, and showing yesterday's numbers as if they
    were live is exactly the class of mistake this project has already made
    twice with stale GPS logs.
    """
    try:
        files = sorted(glob.glob(os.path.join(LIVEDIR, "*.json")),
                       key=os.path.getmtime)
    except OSError:
        return None
    if not files:
        return None
    p = files[-1]
    try:
        age = time.time() - os.path.getmtime(p)
        with open(p) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    d["age"] = round(age, 1)
    d["stale"] = age > 60 and not d.get("finished")
    return d


def annot_list(run):
    """Annotated frames archived by live_survey.py during a live run.

    Named a_NNNNNN.jpg after the recorded frame they came from, so the
    annotated sequence stays aligned with data/frames/<run>/f_NNNNNN.jpg.
    Runs recorded before this was added have none - the page falls back to
    the raw frames and says so.
    """
    out = []
    for q in sorted(glob.glob(os.path.join(LIVEDIR, run, "a_*.jpg"))):
        try:
            if os.path.getsize(q) > 0:
                out.append(q)
        except OSError:
            continue
    return out


def session_live_json(run):
    """The final live_survey.py snapshot for a run: counts, rate, narration."""
    try:
        with open(os.path.join(LIVEDIR, "%s.json" % run)) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def session_detail(run):
    """Everything one lap produced: what the detector saw while driving, what
    it counted, and what it wrote afterwards."""
    live = session_live_json(run)
    survey = read_text(os.path.join(REPORTS, "survey_%s.txt" % run))
    annot = annot_list(run)
    return {
        "id": run,
        "n_annot": len(annot),
        "n_frames": len(frame_list(run)),
        "live": live,
        "counts": parse_counts(survey) or live.get("counts") or {},
        "coverage": parse_coverage(survey, run),
        "survey_text": survey,
        "llm_text": read_text(os.path.join(REPORTS, "llm_report_%s.txt" % run)),
        "narration": live.get("narration") or [],
        "carbon": carbon(parse_counts(survey) or live.get("counts") or {}),
        "track": load_track(run),
        "dets": load_detections(run),
    }


def rig_status():
    if OFFLINE:
        return {"state": "offline", "label": "Offline copy", "offline": True,
                "busy": False, "driving": False, "recording": False,
                "analysing": False, "run": (run_ids() or [None])[0],
                "frames": 0, "lap": None, "analysis": None, "live": None,
                "preview": False,
                "log": "Offline copy of the vehicle data. The rig cannot be "
                       "controlled from here."}
    runs = run_ids()
    cur = runs[0] if runs else None
    driving = proc_running("manage.py drive")
    recording = proc_running("record_survey.py")
    analysing = proc_running("analyze_survey.py")
    busy = proc_running("survey.sh")
    live_now = proc_running("live_survey.py")
    if live_now:
        state, label = "live", "Detecting live"
    elif analysing:
        state, label = "analysing", "Analysing"
    elif recording or driving:
        state, label = "running", "Lap in progress"
    elif busy:
        state, label = "starting", "Starting up"
    else:
        state, label = "idle", "Idle"
    tail = ""
    try:
        with open(RUNLOG) as fh:
            tail = "".join(fh.readlines()[-14:])
    except OSError:
        pass
    return {"state": state, "label": label, "busy": busy,
            "driving": driving, "recording": recording,
            "analysing": analysing or live_now,
            "run": cur,
            "frames": len(frame_list(cur)) if (cur and recording) else 0,
            "offline": False, "live": live_state(),
            "preview": preview_running(),
            "lap": lap_progress(), "log": tail,
            "analysis": analysis_progress() if analysing else
                        (analysis_progress() if state == "idle" else None)}


def _num(v, lo, hi, default, integer=True):
    """Clamp to a sane range. Every control value is numeric, so nothing a
    caller sends can become part of a command as text."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x or x < lo or x > hi:
        return default
    return int(x) if integer else round(x, 2)


PREVIEW_PY = os.path.expanduser("~/oakd_project/camera_preview.py")
PREVIEW_JPG = os.path.join(DATA, "live", "preview.jpg")
PYBIN = os.path.expanduser("~/obj-detection-env/bin/python")


def preview_running():
    return proc_running("camera_preview.py")


def preview_start():
    if OFFLINE:
        return {"ok": False, "error": "This is an offline copy - no camera here."}
    if proc_running("record_survey.py"):
        return {"ok": False,
                "error": "A run is recording. The OAK-D allows one owner at a "
                         "time, so the preview cannot open it now."}
    if preview_running():
        return {"ok": True, "already": True}
    try:
        fh = open(os.path.expanduser("~/preview.log"), "wb")
        subprocess.Popen([PYBIN, "-u", PREVIEW_PY],
                         stdout=fh, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return {"ok": False, "error": "could not start: %s" % e}
    return {"ok": True}


def preview_stop(wait_release=True):
    """Stop the preview and wait for the OAK-D to come back.

    Waiting is the whole point. depthai needs a moment to re-enumerate after a
    process releases it; starting a lap too soon fails with 'No DepthAI device
    found!' even though lsusb still lists the camera.
    """
    if not preview_running():
        return {"ok": True, "already_stopped": True}
    for sig in ("-TERM", "-KILL"):
        try:
            subprocess.run(["pkill", sig, "-f", "camera_preview.py"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        for _ in range(12):
            if not preview_running():
                break
            time.sleep(0.5)
        if not preview_running():
            break
    try:
        os.remove(PREVIEW_JPG)
    except OSError:
        pass
    if wait_release:
        time.sleep(3)
    return {"ok": True, "released": not preview_running()}


def start_lap(body):
    if OFFLINE:
        return {"ok": False, "error": "This is an offline copy - no vehicle here."}
    if proc_running("survey.sh") or proc_running("record_survey.py"):
        return {"ok": False, "error": "A run is already in progress."}
    # INTERLOCK: release the camera before the recorder needs it.
    if preview_running():
        preview_stop()
    fps = _num(body.get("fps"), 1, 30, 4)
    laps = _num(body.get("laps"), 1, 10, 1)
    every = _num(body.get("every"), 0, 60, 0)
    timeout = _num(body.get("timeout"), 60, 3600, 1200)
    radius = _num(body.get("radius"), 0.5, 25, 3.0, integer=False)
    # Detection floor. 0.25 by default: measured on the HAT over the 261-image
    # val split, recall at 0.2 is already only 0.87 for trees, so the old 0.40
    # was dropping real objects on a survey whose job is counting them.
    conf = _num(body.get("conf"), 0.05, 0.9, 0.25, integer=False)
    report = bool(body.get("report", True))
    # Drive itself instead of waiting for the gamepad. The saved path is
    # already loaded by the drive loop on startup, so this only flips the
    # drive mode to Full Auto once everything else is up.
    auto = bool(body.get("auto", False))
    args = [SURVEY_SH, "--fps", str(fps), "--laps", str(laps),
            "--timeout", str(timeout), "--radius", str(radius),
            "--conf", str(conf), "--every", str(every)]
    if auto:
        args.append("--auto")
    # Live mode is the default from the dashboard: it is what shows the
    # detection boxes while the car drives. Without it the page has nothing
    # to display until the whole run has been analysed afterwards.
    if body.get("live", True):
        args.append("--live")
    if not report:
        args.append("--no-report")
    try:
        fh = open(RUNLOG, "wb")
        subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True,
                         cwd=os.path.expanduser("~"))
    except OSError as e:
        return {"ok": False, "error": "could not start: %s" % e}
    return {"ok": True, "started": {"fps": fps, "laps": laps, "every": every,
                                    "radius": radius, "timeout": timeout,
                                    "conf": conf, "report": report, "auto": auto,
                                    "live": bool(body.get("live", True))}}


def stop_lap():
    if OFFLINE:
        return {"ok": False, "error": "This is an offline copy - no vehicle here."}
    """Stop survey.sh first so its own trap tears the rig down cleanly."""
    killed = []
    for pat in ("survey.sh", "lap_watch.py", "record_survey.py",
                "manage.py drive"):
        try:
            r = subprocess.run(["pkill", "-f", pat], timeout=10)
            if r.returncode == 0:
                killed.append(pat)
        except (OSError, subprocess.SubprocessError):
            pass
    return {"ok": True, "stopped": killed}


def run_detail(run):
    survey = read_text(os.path.join(REPORTS, "survey_%s.txt" % run))
    llm = read_text(os.path.join(REPORTS, "llm_report_%s.txt" % run))
    frames = frame_list(run)
    return {
        "id": run,
        "n_frames": len(frames),
        "survey_text": survey,
        "llm_text": llm,
        "counts": parse_counts(survey),
        "coverage": parse_coverage(survey, run),
        "dets": load_detections(run),
        "track": load_track(run),
    }


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grove &mdash; Robocar Survey</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Karla:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{
  color-scheme: light;
  --bark:#14201a; --bark-2:#3d4f45; --bark-3:#6c7d72;
  --canvas:#eef1ea; --card:#fbfcf9; --sunk:#e3e8dd; --edge:#d2dac9;
  --moss:#0a7a45; --moss-deep:#065c34; --moss-wash:#dcece2;
  --brass:#9a7b2e; --brass-wash:#f0e8d4;
  --trees:#0a7a45; --shrubs:#d9a03e; --people:#6f5bd0;
  --alert:#a6401f;
  --lift:0 1px 2px rgba(20,32,26,.05), 0 12px 32px -20px rgba(20,32,26,.35);
  --lift-lg:0 2px 4px rgba(20,32,26,.06), 0 28px 60px -34px rgba(20,32,26,.45);
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  color-scheme: dark;
  --bark:#eef3ec; --bark-2:#b3c2b7; --bark-3:#7f8f84;
  --canvas:#0d120f; --card:#151d18; --sunk:#101711; --edge:#26332b;
  --moss:#3fbf80; --moss-deep:#2aa068; --moss-wash:#10281c;
  --brass:#d8b968; --brass-wash:#2a2416;
  --trees:#22a066; --shrubs:#b8862f; --people:#8f78d4;
  --alert:#e0714a;
  --lift:0 1px 2px rgba(0,0,0,.5), 0 14px 34px -22px rgba(0,0,0,.9);
  --lift-lg:0 2px 6px rgba(0,0,0,.55), 0 30px 64px -36px rgba(0,0,0,1);
}}
:root[data-theme="dark"]{
  color-scheme: dark;
  --bark:#eef3ec; --bark-2:#b3c2b7; --bark-3:#7f8f84;
  --canvas:#0d120f; --card:#151d18; --sunk:#101711; --edge:#26332b;
  --moss:#3fbf80; --moss-deep:#2aa068; --moss-wash:#10281c;
  --brass:#d8b968; --brass-wash:#2a2416;
  --trees:#22a066; --shrubs:#b8862f; --people:#8f78d4;
  --alert:#e0714a;
  --lift:0 1px 2px rgba(0,0,0,.5), 0 14px 34px -22px rgba(0,0,0,.9);
  --lift-lg:0 2px 6px rgba(0,0,0,.55), 0 30px 64px -36px rgba(0,0,0,1);
}

*{box-sizing:border-box}
html,body{margin:0}
body{
  background:var(--canvas); color:var(--bark);
  font:16px/1.6 Karla, ui-sans-serif, system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing:antialiased;
}
.mono{font-family:"JetBrains Mono", ui-monospace, SFMono-Regular, monospace;
  font-variant-numeric:tabular-nums}
.wrap{max-width:1240px;margin:0 auto;padding:0 26px 72px}
@media(max-width:700px){.wrap{padding:0 15px 48px}}

/* ---------- masthead ---------- */
header{
  display:flex;align-items:center;gap:18px;flex-wrap:wrap;
  padding:24px 0 18px;
}
.brand{display:flex;align-items:baseline;gap:11px;margin-right:auto}
.brand h1{
  font-family:Fraunces, Georgia, serif; font-optical-sizing:auto;
  font-weight:600; font-size:29px; letter-spacing:-.018em; margin:0;
}
.brand .sub{font-size:12.5px;color:var(--bark-3);letter-spacing:.04em}
.leaf{width:26px;height:26px;flex:none;color:var(--moss)}

/* ---------- view switch ---------- */
.views{display:flex;gap:3px;background:var(--sunk);border:1px solid var(--edge);
  border-radius:999px;padding:3px}
.views button{
  border:0;background:transparent;color:var(--bark-3);font:inherit;
  font-weight:600;font-size:13.5px;padding:7px 17px;border-radius:999px;
  cursor:pointer;transition:background .18s,color .18s;white-space:nowrap;
}
.views button[aria-selected="true"]{background:var(--card);color:var(--bark);
  box-shadow:var(--lift)}
.views button:focus-visible{outline:2px solid var(--moss);outline-offset:2px}

.iconbtn{
  border:1px solid var(--edge);background:var(--card);color:var(--bark-2);
  border-radius:10px;width:36px;height:36px;cursor:pointer;font-size:15px;
  display:grid;place-items:center;
}
.iconbtn:hover{color:var(--bark)}
select#run{
  font:inherit;font-size:13.5px;padding:8px 12px;border-radius:10px;
  border:1px solid var(--edge);background:var(--card);color:var(--bark);
  max-width:min(420px,52vw);
}

/* ---------- cards ---------- */
.card{
  background:var(--card);border:1px solid var(--edge);border-radius:16px;
  box-shadow:var(--lift);overflow:hidden;
}
.card > h2{
  font-family:Fraunces, Georgia, serif; font-weight:600; font-size:15px;
  letter-spacing:.01em; margin:0; padding:15px 20px 13px;
  border-bottom:1px solid var(--edge);
  display:flex;align-items:center;gap:9px;
}
.card .body{padding:20px}
.card .body.flush{padding:0}

/* ---------- live hero ---------- */
.hero{display:grid;grid-template-columns:1.55fr 1fr;gap:20px;align-items:start}
@media(max-width:980px){.hero{grid-template-columns:1fr}}
.shotwrap{position:relative;background:#000;aspect-ratio:16/9;overflow:hidden}
.shotwrap img{width:100%;height:100%;display:block;object-fit:cover}
.shotwrap .placeholder{
  position:absolute;inset:0;display:grid;place-items:center;text-align:center;
  color:var(--bark-3);font-size:13.5px;padding:24px;background:var(--sunk);
}
.badge{
  position:absolute;top:12px;left:12px;display:flex;align-items:center;gap:7px;
  background:rgba(10,20,14,.72);color:#fff;backdrop-filter:blur(8px);
  padding:5px 11px;border-radius:999px;font-size:11.5px;font-weight:700;
  letter-spacing:.09em;text-transform:uppercase;
}
.pulse{width:7px;height:7px;border-radius:50%;background:#4ade80;
  animation:pulse 1.7s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.8)}}

/* ---------- tallies ---------- */
.tally{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;
  background:var(--edge);border-top:1px solid var(--edge)}
.tally .t{background:var(--card);padding:15px 16px}
.tally .k{display:flex;align-items:center;gap:7px;font-size:11.5px;
  letter-spacing:.09em;text-transform:uppercase;color:var(--bark-3);
  font-weight:700;margin-bottom:5px}
.swatch{width:10px;height:10px;border-radius:3px;flex:none}
.tally .v{font-family:Fraunces,Georgia,serif;font-size:32px;font-weight:600;
  line-height:1;font-variant-numeric:tabular-nums}
.tally .r{font-size:11.5px;color:var(--bark-3);margin-top:3px}
.tally .t.bump .v{animation:pop .5s ease}
@keyframes pop{0%{transform:scale(1)}38%{transform:scale(1.22)}100%{transform:scale(1)}}

/* ---------- progress ---------- */
.meter{height:7px;border-radius:99px;background:var(--sunk);overflow:hidden;
  border:1px solid var(--edge)}
.meter>i{display:block;height:100%;background:linear-gradient(90deg,var(--moss-deep),var(--moss));
  width:0;transition:width .6s cubic-bezier(.2,.7,.3,1)}
.rowline{display:flex;justify-content:space-between;gap:12px;font-size:12.5px;
  color:var(--bark-3);margin-top:8px}

/* ---------- narration ---------- */
.narr{display:flex;flex-direction:column;gap:0}
.narr .n{padding:12px 0;border-bottom:1px dashed var(--edge);display:flex;gap:12px}
.narr .n:last-child{border-bottom:0}
.narr .n .t{font-size:11px;color:var(--bark-3);flex:none;width:46px;padding-top:2px}
.narr .n .x{font-size:14.5px;line-height:1.5}
.narr .n:first-child .x{color:var(--bark);font-weight:500}
.narr .empty{color:var(--bark-3);font-size:13.5px;padding:6px 0}

/* ---------- controls ---------- */
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:13px 15px}
.fields label{display:flex;flex-direction:column;gap:5px;font-size:11.5px;
  color:var(--bark-3);font-weight:700;letter-spacing:.05em;text-transform:uppercase}
.fields input,.fields select{
  width:100%;padding:9px 11px;border:1px solid var(--edge);border-radius:10px;
  background:var(--canvas);color:var(--bark);font:inherit;font-size:14.5px;
  font-weight:400;letter-spacing:0;text-transform:none;
}
.fields input:focus,.fields select:focus{outline:2px solid var(--moss);outline-offset:1px}
.fields .hint{font-weight:400;letter-spacing:0;text-transform:none;
  color:var(--bark-3);font-size:11px}
.btns{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.btn{
  padding:11px 20px;border-radius:11px;border:1px solid var(--edge);
  background:var(--card);color:var(--bark);font:inherit;font-weight:700;
  font-size:14px;cursor:pointer;transition:transform .12s,box-shadow .18s;
}
.btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:var(--lift)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn:focus-visible{outline:2px solid var(--moss);outline-offset:2px}
.btn.go{background:var(--moss-deep);border-color:var(--moss-deep);color:#fff}
.btn.halt{border-color:var(--alert);color:var(--alert);background:transparent}
#ctlmsg{font-size:13px;margin-top:12px;min-height:1.3em;color:var(--bark-2)}

/* ---------- archive ---------- */
.arch{display:grid;grid-template-columns:1.4fr 1fr;gap:20px;align-items:start}
@media(max-width:980px){.arch{grid-template-columns:1fr}}
.tabs{display:flex;gap:2px;padding:4px;background:var(--sunk);
  border-bottom:1px solid var(--edge)}
.tabs .tab{flex:1;text-align:center;padding:9px;border-radius:9px;cursor:pointer;
  font-size:13px;font-weight:600;color:var(--bark-3)}
.tabs .tab[aria-selected="true"]{background:var(--card);color:var(--bark);
  box-shadow:var(--lift)}
pre#report,pre#sreport{margin:0;padding:20px;
  font-family:"JetBrains Mono",ui-monospace,monospace;
  font-size:12.5px;line-height:1.65;white-space:pre-wrap;color:var(--bark-2);
  overflow-wrap:anywhere;max-height:560px;overflow:auto}
.ctl{display:flex;align-items:center;gap:12px;padding:14px 18px;
  border-top:1px solid var(--edge)}
.ctl input[type=range]{flex:1;accent-color:var(--moss)}
.playbtn{width:38px;height:38px;border-radius:50%;border:1px solid var(--edge);
  background:var(--canvas);color:var(--bark);cursor:pointer;font-size:13px}
#map svg{width:100%;height:auto;display:block}
.legend{display:flex;gap:16px;flex-wrap:wrap;padding:0 20px 16px;font-size:12.5px;
  color:var(--bark-2)}
.legend span{display:inline-flex;align-items:center;gap:7px}
.note{padding:0 20px 18px;font-size:12px;color:var(--bark-3);line-height:1.5}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:14px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--edge);border-radius:14px;
  padding:16px 18px;box-shadow:var(--lift)}
.kpi .k{font-size:11px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--bark-3);font-weight:700;margin-bottom:6px;display:flex;
  align-items:center;gap:7px}
.kpi .v{font-family:Fraunces,Georgia,serif;font-size:30px;font-weight:600;
  line-height:1;font-variant-numeric:tabular-nums}
.kpi .s{font-size:11.5px;color:var(--bark-3);margin-top:5px}
.empty{color:var(--bark-3);font-size:13.5px;padding:26px;text-align:center}
.hide{display:none!important}
.stack{display:flex;flex-direction:column;gap:20px}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

.steps{background:var(--moss-wash);border:1px solid var(--moss);border-radius:12px;
  padding:14px 16px;margin-bottom:16px}
.steps h4{margin:0 0 8px;font-family:Fraunces,Georgia,serif;font-size:15px;
  font-weight:600;color:var(--moss-deep)}
:root[data-theme=dark] .steps h4,
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .steps h4{color:var(--moss)}}
.steps ol{margin:0;padding-left:19px;font-size:14px;line-height:1.75}
.steps kbd{font-family:"JetBrains Mono",monospace;font-size:12px;font-weight:600;
  background:var(--card);border:1px solid var(--edge);border-radius:5px;padding:1px 6px}
.sessgrid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
  gap:20px;padding:0 20px 20px}
@media(max-width:900px){.sessgrid{grid-template-columns:minmax(0,1fr)}}
.card.flat{box-shadow:none;border-radius:12px}
#sesscard > h2 > select{font-family:Karla,system-ui,sans-serif;font-size:12.5px;
  padding:5px 9px;border-radius:8px;border:1px solid var(--edge);
  background:var(--card);color:var(--bark);max-width:46%}
#sreport{max-height:460px}
.narrline{display:flex;gap:11px;padding:7px 0;border-bottom:1px solid var(--edge);
  font-size:13px;line-height:1.55;color:var(--bark-2)}
.narrline:last-child{border-bottom:0}
.narrline b{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11.5px;
  color:var(--bark-3);font-weight:500;flex:0 0 5ch;text-align:right;
  font-variant-numeric:tabular-nums}
#runlog{margin:14px 0 0;padding:12px 13px;background:var(--sunk);
  border:1px solid var(--edge);border-radius:10px;font-size:11.5px;line-height:1.55;
  max-height:150px;overflow:auto;white-space:pre-wrap;color:var(--bark-3);
  font-family:"JetBrains Mono",ui-monospace,monospace}
</style>
<div class="wrap">
<header>
  <div class="brand">
    <svg class="leaf" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M20 4C10 4 4 9.5 4 17c0 1 .1 2 .4 3M4 20c8 0 16-4.5 16-16"
            stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
      <path d="M12 12c2.6 0 5-1 6.5-2.6" stroke="currentColor" stroke-width="1.3"
            stroke-linecap="round" opacity=".55"/>
    </svg>
    <h1>Grove</h1>
    <span class="sub">Robocar tree survey</span>
  </div>
  <div class="views" role="tablist">
    <button id="v-live" role="tab" aria-selected="true">Session</button>
    <button id="v-arch" role="tab" aria-selected="false">Archive</button>
  </div>
  <select id="run" class="hide" aria-label="Recorded run"></select>
  <button id="theme" class="iconbtn" title="Light or dark">&#9681;</button>
</header>

<!-- ================= LIVE ================= -->
<section id="view-live">
  <div class="hero">
    <div class="stack">
      <div class="card">
        <h2>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="3" y="6" width="18" height="13" rx="2.5" stroke="currentColor" stroke-width="1.8"/>
            <circle cx="12" cy="12.5" r="3.4" stroke="currentColor" stroke-width="1.8"/>
            <path d="M8.5 6l1.2-2h4.6L15.5 6" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          </svg>
          <span id="feedtitle">Camera</span>
        </h2>
        <div class="body flush">
          <div class="shotwrap">
            <img id="feed" alt="live camera view with detections" class="hide">
            <div class="placeholder" id="feednote">
              Press <strong>Camera view</strong> to look through the lens,
              or <strong>Start lap</strong> to begin a survey.
            </div>
            <div class="badge hide" id="feedbadge"><span class="pulse"></span><span id="feedbadgetxt">live</span></div>
          </div>
          <div class="tally" id="tally"></div>
        </div>
      </div>

      <div class="card" id="progcard">
        <h2>Progress</h2>
        <div class="body">
          <div id="stepnow" class="steps hide"></div>
          <div class="meter"><i id="lapbar"></i></div>
          <div class="rowline"><span id="lapleft">no lap in progress</span><span id="lapright" class="mono"></span></div>
          <div class="meter" style="margin-top:15px"><i id="anabar"></i></div>
          <div class="rowline"><span id="analeft">detection idle</span><span id="anaright" class="mono"></span></div>
          <pre id="runlog" class="hide"></pre>
        </div>
      </div>
    </div>

    <div class="stack">
      <div class="card">
        <h2>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M5 5h14M5 10h14M5 15h9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
          </svg>
          As it happens
        </h2>
        <div class="body"><div class="narr" id="narr"><div class="empty">The written report builds here while the car drives.</div></div></div>
      </div>

      <div class="card">
        <h2>Rig control</h2>
        <div class="body">
          <div class="fields">
            <label>Camera fps
              <input type="number" id="f-fps" value="1" min="1" max="30" step="1">
              <span class="hint">1 keeps detection in step</span></label>
            <label>Laps
              <input type="number" id="f-laps" value="1" min="1" max="10" step="1"></label>
            <label>Merge radius (m)
              <input type="number" id="f-radius" value="3" min="0.5" max="25" step="0.5">
              <span class="hint">closer than this counts once</span></label>
            <label>Timeout (s)
              <input type="number" id="f-timeout" value="1200" min="60" max="3600" step="60"></label>
            <label>Live detection
              <select id="f-live"><option value="1" selected>on</option><option value="0">off</option></select>
              <span class="hint">boxes while driving</span></label>
            <label>Written report
              <select id="f-report"><option value="1" selected>on</option><option value="0">off</option></select></label>
            <label>Detection floor
              <input type="number" id="f-conf" value="0.25" min="0.05" max="0.9" step="0.05">
              <span class="hint">below 0.25 finds more, and more false ones</span></label>
            <label>Start driving
              <select id="f-auto">
                <option value="1" selected>automatically</option>
                <option value="0">on the controller</option>
              </select>
              <span class="hint">auto: the car pulls away on its own</span></label>
          </div>
          <div class="btns">
            <button class="btn" id="b-prev">Camera view</button>
            <button class="btn go" id="b-start">Start lap</button>
            <button class="btn halt" id="b-stop">Stop</button>
          </div>
          <div id="ctlmsg"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ---- what the lap left behind: video + report, saved ---- -->
  <div class="card hide" id="sesscard" style="margin-top:20px">
    <h2>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M4 7.5A2.5 2.5 0 016.5 5h6L15 7.5h2.5A2.5 2.5 0 0120 10v7a2 2 0 01-2 2H6a2 2 0 01-2-2z"
              stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>
        <path d="M10.5 11.5l4 2.2-4 2.2z" fill="currentColor"/>
      </svg>
      <span id="sesstitle">Saved lap</span>
      <select id="sesspick" aria-label="Saved lap" style="margin-left:auto"></select>
    </h2>
    <div class="body">
      <div class="kpis" id="sesskpis" style="margin-bottom:18px"></div>
    </div>
    <div class="sessgrid">
      <div class="stack">
        <div class="card flat">
          <h2>Detection video</h2>
          <div class="body flush">
            <div class="shotwrap">
              <img id="sshot" alt="annotated frame from the lap">
              <div class="placeholder" id="snote">No saved video for this lap.</div>
            </div>
            <div class="ctl">
              <button class="playbtn" id="splay">&#9654;</button>
              <input type="range" id="sslider" min="0" value="0" aria-label="Frame">
              <span id="sfnum" class="mono" style="min-width:9ch;text-align:right;font-size:12.5px"></span>
            </div>
          </div>
          <div class="note" id="sfallback"></div>
        </div>
      </div>
      <div class="card flat">
        <div class="tabs">
          <div class="tab" id="s-llm" role="tab" aria-selected="true">Written report</div>
          <div class="tab" id="s-num" role="tab" aria-selected="false">Numbers</div>
          <div class="tab" id="s-nar" role="tab" aria-selected="false">As it happened</div>
        </div>
        <pre id="sreport" class="mono"></pre>
      </div>
    </div>
  </div>
</section>

<!-- ================= ARCHIVE ================= -->
<section id="view-arch" class="hide">
  <div class="kpis" id="kpis"></div>
  <div class="arch">
    <div class="stack">
      <div class="card">
        <h2>Recorded frames</h2>
        <div class="body flush">
          <div class="shotwrap"><img id="shot" alt="recorded frame"></div>
          <div class="ctl">
            <button class="playbtn" id="play">&#9654;</button>
            <input type="range" id="slider" min="0" value="0" aria-label="Frame">
            <span id="fnum" class="mono" style="min-width:9ch;text-align:right;font-size:12.5px"></span>
          </div>
        </div>
      </div>
      <div class="card">
        <h2>Track &amp; detections</h2>
        <div class="body"><div id="map"></div></div>
        <div class="legend" id="legend"></div>
        <div class="note">Each point marks where the <em>car</em> was when a detection
          fired &mdash; not the surveyed position of the object itself.</div>
      </div>
    </div>
    <div class="card">
      <div class="tabs">
        <div class="tab" id="t-llm" role="tab" aria-selected="true">Written report</div>
        <div class="tab" id="t-num" role="tab" aria-selected="false">Numbers</div>
      </div>
      <pre id="report" class="mono"></pre>
    </div>
  </div>
</section>
</div>
<script>
const $ = s => document.querySelector(s);
const CLASSES = ["trees","shrubs","people"];
let D = null, timer = null, tab = "llm", view = "live";
let lastState = null, prevCounts = {}, feedKind = null, pollTimer = null;

try{ const t = localStorage.getItem("grove-theme");
     if (t) document.documentElement.setAttribute("data-theme", t); }catch(e){}
$("#theme").onclick = () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const dark = cur ? cur === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try{ localStorage.setItem("grove-theme", next); }catch(e){}
};

function setView(v){
  view = v;
  $("#v-live").setAttribute("aria-selected", v === "live");
  $("#v-arch").setAttribute("aria-selected", v === "arch");
  $("#view-live").classList.toggle("hide", v !== "live");
  $("#view-arch").classList.toggle("hide", v !== "arch");
  $("#run").classList.toggle("hide", v !== "arch");
  if (v !== "arch") stopPlay();
}
$("#v-live").onclick = () => setView("live");
$("#v-arch").onclick = () => setView("arch");

/* ---------------- live view ---------------- */
function fmtEta(s){
  if (s === null || s === undefined) return "";
  const m = Math.floor(s/60), r = Math.round(s%60);
  return m ? m+"m "+r+"s" : r+"s";
}
function setFeed(kind, src){
  const im = $("#feed"), ph = $("#feednote"), bd = $("#feedbadge");
  if (kind === feedKind) return;          // never re-attach a live stream
  feedKind = kind;
  if (!kind){ im.src = ""; im.classList.add("hide"); ph.classList.remove("hide");
              bd.classList.add("hide"); return; }
  im.onerror = () => { feedKind = null; };
  im.src = src + "?t=" + Date.now();
  im.classList.remove("hide"); ph.classList.add("hide"); bd.classList.remove("hide");
}
function renderTally(counts, animate){
  const el = $("#tally");
  el.innerHTML = CLASSES.map(k => {
    const c = counts && counts[k];
    const n = c ? c.distinct : 0, raw = c ? c.raw : 0;
    const bump = animate && prevCounts[k] !== undefined && n > prevCounts[k];
    return '<div class="t'+(bump?' bump':'')+'">'
      + '<div class="k"><span class="swatch" style="background:var(--'+k+')"></span>'+k+'</div>'
      + '<div class="v" style="color:var(--'+k+')">'+n+'</div>'
      + '<div class="r">'+raw+' raw hits</div></div>';
  }).join("");
  if (counts) CLASSES.forEach(k => { prevCounts[k] = counts[k] ? counts[k].distinct : 0; });
}
function renderNarr(list){
  const el = $("#narr");
  if (!list || !list.length){
    el.innerHTML = '<div class="empty">The written report builds here while the car drives.</div>';
    return;
  }
  el.innerHTML = list.slice().reverse().map(n =>
    '<div class="n"><span class="t mono">'+fmtEta(n.t)+'</span><span class="x">'
    + n.text.replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])) + '</span></div>'
  ).join("");
}
function pollStatus(){
  fetch("/api/status").then(r => r.json()).then(st => {
    const L = st.live, A = st.analysis;

    /* Feed priority, most-current first. The camera preview is the only
       source that is literally NOW, so it always wins. A finished run must
       never hold the feed: it is never marked stale, so it used to sit there
       showing a frame from minutes ago while the camera was open. */
    const running = L && !L.stale && !L.finished;
    if (st.preview){
      setFeed("preview", "/preview/stream");
      $("#feedtitle").textContent = running
        ? "Camera \u2014 live (detections running)" : "Camera \u2014 live";
      $("#feedbadgetxt").textContent = "camera";
      $("#feedbadge").querySelector(".pulse").style.background = "#4ade80";
    } else if (running){
      setFeed("live", "/live/stream");
      $("#feedtitle").textContent = "Detections \u2014 live";
      $("#feedbadgetxt").textContent = (L.mode === "live") ? "live" : (L.mode || "catching up");
      $("#feedbadge").querySelector(".pulse").style.background = "#4ade80";
    } else if (L && L.finished && st.state === "idle" && !st.recording){
      setFeed("live", "/live/stream");
      $("#feedtitle").textContent = "Detections \u2014 last frame of " + (L.run || "the last run");
      $("#feedbadgetxt").textContent = "run complete";
      $("#feedbadge").querySelector(".pulse").style.background = "var(--brass)";
    } else {
      setFeed(null);
      $("#feedtitle").textContent = "Camera";
    }

    renderTally(L && !L.stale ? L.counts : null, true);
    renderNarr(L && !L.stale ? L.narration : null);

    /* lap meter */
    const lap = st.lap;
    if (lap && lap.from_start !== null){
      $("#lapbar").style.width = lap.pct + "%";
      $("#lapleft").textContent = lap.left_start ? "driving the loop" : "still near the start";
      $("#lapright").textContent = lap.driven + " / " + lap.need + " m  ·  "
        + lap.from_start + " m from start";
    } else {
      $("#lapbar").style.width = "0%";
      $("#lapleft").textContent = st.recording ? "recording — waiting for movement"
                                               : "no lap in progress";
      $("#lapright").textContent = "";
    }

    /* detection meter */
    if (L && !L.stale){
      const pct = L.recorded ? Math.round(100*L.processed/L.recorded) : 0;
      $("#anabar").style.width = pct + "%";
      $("#analeft").textContent = (L.mode === "live")
        ? "detecting the newest frame" : "working through the backlog";
      $("#anaright").textContent = L.processed + " / " + L.recorded + " frames  ·  "
        + L.raw + " hits  ·  " + L.rate + " fps";
    } else if (A && st.analysing){
      $("#anabar").style.width = (A.pct || 0) + "%";
      $("#analeft").textContent = A.phase === "writing"
        ? "writing the report" : "analysing after the lap";
      $("#anaright").textContent = A.done ? (A.done+" / "+A.total+" frames · "+fmtEta(A.eta)+" left") : "";
    } else {
      $("#anabar").style.width = "0%";
      $("#analeft").textContent = "detection idle";
      $("#anaright").textContent = "";
    }

    /* The run is up but the car will not move until the driver acts. Say so
       loudly - the old dashboard showed survey.sh's own output here and the
       redesign dropped it, which made "Start lap" look like it did nothing. */
    const waiting = st.driving && !(lap && lap.left_start);
    const sn = $("#stepnow");
    if (waiting){
      sn.classList.remove("hide");
      sn.innerHTML = '<h4>Your turn \u2014 on the controller</h4><ol>'
        + '<li>Press <kbd>X</kbd> to load the saved path</li>'
        + '<li>Press <kbd>start</kbd> until the mode reaches <strong>Full Auto</strong></li>'
        + '<li>Any stick input takes manual control back instantly</li></ol>';
    } else { sn.classList.add("hide"); }

    const rl = $("#runlog");
    if (st.log && st.log.trim() && st.state !== "idle"){
      rl.classList.remove("hide");
      if (rl.textContent !== st.log){
        rl.textContent = st.log;
        rl.scrollTop = rl.scrollHeight;
      }
    } else { rl.classList.add("hide"); }

    /* buttons */
    const busy = st.state !== "idle" && st.state !== "offline";
    $("#b-start").disabled = busy || st.offline;
    $("#b-stop").disabled  = !busy;
    $("#b-prev").disabled  = st.offline || st.recording;
    $("#b-prev").textContent = st.preview ? "Stop camera view" : "Camera view";
    if (st.offline && !$("#ctlmsg").textContent)
      $("#ctlmsg").textContent = "Offline copy — viewing synced data.";

    if (lastState && lastState !== "idle" && st.state === "idle" && !st.offline){
      loadRuns(true);
      // The lap just finished. The written report is produced after the car
      // stops, so give it a moment before pulling the session in.
      loadSessions(false);
      setTimeout(() => loadSessions(false), 12000);
    }
    lastState = st.state;

    const want = busy ? 1000 : 3000;
    if (pollTimer && pollTimer._ms !== want){
      clearInterval(pollTimer);
      pollTimer = setInterval(pollStatus, want); pollTimer._ms = want;
    }
  }).catch(() => {});
}
function post(path, body){
  return fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
                      body: body ? JSON.stringify(body) : null}).then(r => r.json());
}
$("#b-prev").onclick = () => {
  const on = $("#b-prev").textContent.startsWith("Stop");
  $("#b-prev").disabled = true;
  $("#ctlmsg").textContent = on ? "releasing the camera…" : "opening the camera…";
  post("/api/control/" + (on ? "preview_off" : "preview_on")).then(j => {
    $("#ctlmsg").textContent = j.ok
      ? (on ? "Camera released." : "Camera on. It hands over automatically when you start a lap.")
      : ("Could not switch the camera: " + (j.error || "unknown"));
    feedKind = null; pollStatus();
  }).catch(e => $("#ctlmsg").textContent = "request failed: " + e);
};
$("#b-start").onclick = () => {
  $("#b-start").disabled = true;
  $("#ctlmsg").textContent = "starting…";
  post("/api/control/start", {
    fps:+$("#f-fps").value, laps:+$("#f-laps").value, radius:+$("#f-radius").value,
    timeout:+$("#f-timeout").value, live:$("#f-live").value==="1",
    report:$("#f-report").value==="1", conf:+$("#f-conf").value,
    auto:$("#f-auto").value==="1"
  }).then(j => {
    $("#ctlmsg").textContent = j.ok
      ? "Recording at " + j.started.fps + " fps"
        + (j.started.live ? " with live detection" : "")
        + (j.started.auto
            ? ". The car will pull away by itself once GPS is ready \u2014 stand clear. Stop ends it."
            : ". On the controller: press start to cycle to Full Auto.")
      : ("Could not start: " + (j.error || "unknown"));
    feedKind = null; pollStatus();
  }).catch(e => $("#ctlmsg").textContent = "request failed: " + e);
};
$("#b-stop").onclick = () => {
  $("#ctlmsg").textContent = "stopping…";
  post("/api/control/stop").then(j => {
    $("#ctlmsg").textContent = "Stopped: " + ((j.stopped||[]).join(", ") || "nothing was running");
    feedKind = null; pollStatus();
  }).catch(e => $("#ctlmsg").textContent = "request failed: " + e);
};

/* ---------------- archive ---------------- */
function loadRuns(keep){
  const cur = $("#run").value;
  return fetch("/api/runs").then(r => r.json()).then(rs => {
    const sel = $("#run");
    sel.innerHTML = "";
    rs.forEach(r => {
      const o = document.createElement("option");
      o.value = r.id;
      o.textContent = r.id + "  ·  " + r.n_frames + " frames"
        + (r.span ? "  ·  " + r.span : "")
        + (r.gps ? "  ·  GPS" : "  ·  no GPS")
        + (r.report ? "  ·  analysed" : "");
      sel.appendChild(o);
    });
    const pick = (keep && cur && rs.some(r => r.id === cur)) ? cur
      : ((rs.find(r => r.gps && r.report) || rs.find(r => r.gps) || rs[0] || {}).id);
    if (pick){ sel.value = pick; loadRun(pick); }
    else $("#kpis").innerHTML = '<div class="kpi"><div class="empty">No runs recorded yet.</div></div>';
  });
}
$("#run").onchange = () => loadRun($("#run").value);

function loadRun(run){
  stopPlay();
  fetch("/api/run/" + encodeURIComponent(run)).then(r => r.json()).then(d => {
    D = d;
    const c = d.counts || {}, cv = d.coverage;
    let k = CLASSES.filter(x => c[x]).map(x =>
      '<div class="kpi"><div class="k"><span class="swatch" style="background:var(--'+x+')"></span>'+x+'</div>'
      + '<div class="v" style="color:var(--'+x+')">'+c[x].distinct+'</div>'
      + '<div class="s">'+c[x].raw+' raw detections</div></div>').join("");
    if (cv) k += '<div class="kpi"><div class="k">Frames analysed</div><div class="v">'
      + cv.analysed + '</div><div class="s">of ' + cv.recorded + ' recorded · '
      + cv.pct + '%' + (cv.stale ? ' · report predates fix' : '') + '</div></div>';
    $("#kpis").innerHTML = k || '<div class="kpi"><div class="empty">This run has not been analysed.</div></div>';
    $("#slider").max = Math.max(0, (d.n_frames||1) - 1);
    show(0); drawMap(); reports();
  });
}
function show(i){
  if (!D || !D.n_frames) return;
  $("#shot").src = "/frame/" + encodeURIComponent(D.id) + "/" + i;
  $("#fnum").textContent = (i+1) + " / " + D.n_frames;
  $("#slider").value = i;
}
$("#slider").oninput = e => { stopPlay(); show(+e.target.value); };
function stopPlay(){ if (timer){ clearInterval(timer); timer = null; $("#play").innerHTML = "&#9654;"; } }
$("#play").onclick = () => {
  if (timer) return stopPlay();
  $("#play").innerHTML = "&#10073;&#10073;";
  timer = setInterval(() => {
    let i = +$("#slider").value + 1;
    if (i > +$("#slider").max) i = 0;
    show(i);
  }, 110);
};
function drawMap(){
  const el = $("#map"), pts = D.track || [], dets = D.dets || [];
  if (!pts.length && !dets.length){
    el.innerHTML = '<div class="empty">No GPS track for this run.</div>';
    $("#legend").innerHTML = ""; return;
  }
  const all = pts.concat(dets.map(d => [d[0], d[1]]));
  let xs = all.map(p=>p[0]), ys = all.map(p=>p[1]);
  let x0=Math.min(...xs), x1=Math.max(...xs), y0=Math.min(...ys), y1=Math.max(...ys);
  const pad = Math.max(x1-x0, y1-y0)*0.12 + 1;
  x0-=pad; x1+=pad; y0-=pad; y1+=pad;
  const W=560, H=380;
  const sx = v => (v-x0)/(x1-x0)*W, sy = v => H-(v-y0)/(y1-y0)*H;
  let s = '<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="GPS track with detections">';
  if (pts.length > 1)
    s += '<polyline fill="none" stroke="var(--edge)" stroke-width="2.5" stroke-linejoin="round" points="'
       + pts.map(p => sx(p[0]).toFixed(1)+","+sy(p[1]).toFixed(1)).join(" ") + '"/>';
  dets.forEach(d => {
    s += '<circle cx="'+sx(d[0]).toFixed(1)+'" cy="'+sy(d[1]).toFixed(1)
       + '" r="4.2" fill="var(--'+d[2]+',var(--bark-3))" fill-opacity=".75"'
       + ' stroke="var(--card)" stroke-width="1.6"><title>'+d[2]+'</title></circle>';
  });
  s += '<text x="9" y="19" fill="var(--bark-3)" font-size="11" font-family="Karla">north up · '
     + (x1-x0).toFixed(0) + ' m across</text></svg>';
  el.innerHTML = s;
  $("#legend").innerHTML = CLASSES.filter(k => (D.counts||{})[k])
    .map(k => '<span><span class="swatch" style="background:var(--'+k+')"></span>'+k+'</span>').join("")
    + '<span><span class="swatch" style="background:var(--edge)"></span>car path</span>';
}
function reports(){
  $("#t-llm").setAttribute("aria-selected", tab === "llm");
  $("#t-num").setAttribute("aria-selected", tab === "num");
  const t = tab === "llm" ? D.llm_text : D.survey_text;
  $("#report").textContent = t && t.trim() ? t
    : (tab === "llm"
       ? "No written report saved for this run.\n\nGenerate one:\n\n  ~/report_survey.sh " + D.id + " 3 3 2"
       : "No numeric report yet. Run ~/report_survey.sh");
}
$("#t-llm").onclick = () => { tab = "llm"; reports(); };
$("#t-num").onclick = () => { tab = "num"; reports(); };

/* ---------------- saved session: video + report on this page -------------- */
let S = null, sTimer = null, sTab = "llm";

function sStopPlay(){
  if (sTimer){ clearInterval(sTimer); sTimer = null; $("#splay").innerHTML = "&#9654;"; }
}
function sShow(i){
  if (!S || !S.n_shown) return;
  i = Math.max(0, Math.min(S.n_shown - 1, i));
  $("#sslider").value = i;
  $("#sshot").src = S.frameUrl + i;
  $("#sfnum").textContent = (i + 1) + " / " + S.n_shown;
}
$("#sslider").oninput = e => { sStopPlay(); sShow(+e.target.value); };
$("#splay").onclick = () => {
  if (sTimer) return sStopPlay();
  if (!S || !S.n_shown) return;
  $("#splay").innerHTML = "&#10073;&#10073;";
  sTimer = setInterval(() => {
    let i = +$("#sslider").value + 1;
    if (i >= S.n_shown){ sStopPlay(); i = S.n_shown - 1; }
    sShow(i);
  }, 180);
};

function sReports(){
  ["llm","num","nar"].forEach(k =>
    $("#s-" + k).setAttribute("aria-selected", sTab === k));
  const el = $("#sreport");
  if (sTab === "nar"){
    const n = (S && S.narration) || [];
    el.innerHTML = n.length
      ? n.map(x => '<div class="narrline"><b>' + fmtEta(x.t) + "</b><span>"
          + esc(x.text) + "</span></div>").join("")
      : '<div class="empty">Nothing was narrated during this lap.</div>';
    el.classList.remove("mono");
    el.style.whiteSpace = "normal";
    el.style.fontFamily = "Karla,system-ui,sans-serif";
    return;
  }
  el.classList.add("mono");
  el.style.whiteSpace = "";
  el.style.fontFamily = "";
  const t = sTab === "llm" ? (S && S.llm_text) : (S && S.survey_text);
  el.textContent = t || (sTab === "llm"
    ? "No written report for this lap."
    : "No numeric report for this lap.");
}
["llm","num","nar"].forEach(k =>
  $("#s-" + k).onclick = () => { sTab = k; sReports(); });

function esc(x){
  return String(x).replace(/[&<>"]/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

function loadSessions(keep){
  const cur = $("#sesspick").value;
  return fetch("/api/runs").then(r => r.json()).then(rs => {
    const sel = $("#sesspick");
    if (!rs.length){ $("#sesscard").classList.add("hide"); return; }
    sel.innerHTML = "";
    rs.forEach(r => {
      const o = document.createElement("option");
      o.value = r.id;
      o.textContent = r.id + (r.n_annot ? "  ·  video" : "")
        + (r.report ? "  ·  report" : "");
      sel.appendChild(o);
    });
    /* After a lap, land on the newest run. rs is newest-first. */
    const pick = (keep && cur && rs.some(r => r.id === cur)) ? cur : rs[0].id;
    sel.value = pick;
    $("#sesscard").classList.remove("hide");
    return loadSession(pick);
  }).catch(() => {});
}
$("#sesspick").onchange = () => loadSession($("#sesspick").value);

function loadSession(run){
  sStopPlay();
  return fetch("/api/session/" + encodeURIComponent(run))
    .then(r => r.json()).then(d => {
      S = d;
      $("#sesstitle").textContent = "Saved lap";

      /* Annotated frames are what the detector actually saw. Runs recorded
         before they were archived fall back to the raw frames, and say so. */
      const annot = d.n_annot > 0;
      S.n_shown = annot ? d.n_annot : d.n_frames;
      S.frameUrl = (annot ? "/aframe/" : "/frame/") + encodeURIComponent(run) + "/";
      $("#sfallback").textContent = annot
        ? "Every frame the detector processed, with its boxes."
        : (d.n_frames ? "No annotated video for this lap - showing the raw "
             + "recording instead. Laps run from now on keep the boxes." : "");
      $("#snote").classList.toggle("hide", S.n_shown > 0);
      $("#sshot").classList.toggle("hide", !S.n_shown);
      $("#splay").disabled = !S.n_shown;
      $("#sslider").disabled = !S.n_shown;
      $("#sslider").max = Math.max(0, S.n_shown - 1);
      if (S.n_shown) sShow(0); else $("#sfnum").textContent = "";

      const c = d.counts || {}, cov = d.coverage || {};
      const live = d.live || {};
      const kpi = (k, v, sub) => '<div class="kpi"><div class="k">' + k
        + '</div><div class="v">' + v + '</div>'
        + (sub ? '<div class="s">' + sub + "</div>" : "") + "</div>";
      let html = "";
      CLASSES.forEach(cl => {
        if (!c[cl]) return;
        html += '<div class="kpi"><div class="k">'
          + '<span class="swatch" style="background:var(--' + cl + ')"></span>' + cl
          + '</div><div class="v" style="color:var(--' + cl + ')">'
          + c[cl].distinct + '</div><div class="s">' + c[cl].raw
          + ' raw detections</div></div>';
      });
      if (cov.pct !== undefined && cov.pct !== null)
        html += kpi("coverage", cov.pct + "%",
          cov.analysed + " of " + cov.recorded + " frames analysed"
          + (cov.stale ? " (report overstated this)" : ""));
      if (live.rate) html += kpi("detection", live.rate + " fps", live.backend || "");
      if (S.n_shown) html += kpi("frames", S.n_shown, annot ? "annotated" : "recorded");
      const cb = d.carbon;
      if (cb){
        const good = cb.net >= 0;
        html += '<div class="kpi"><div class="k">carbon / yr</div>'
          + '<div class="v" style="color:var(--' + (good ? "trees" : "alert") + ')">'
          + (cb.net > 0 ? "+" : "") + cb.net.toLocaleString() + ' kg</div>'
          + '<div class="s">' + cb.absorbed.toLocaleString() + ' absorbed \u2212 '
          + cb.emitted.toLocaleString() + ' emitted<br>rough averages, not measured</div></div>';
      }
      $("#sesskpis").innerHTML = html
        || '<div class="kpi"><div class="empty">This lap produced no counts yet.</div></div>';

      sReports();
    }).catch(() => {});
}

renderTally(null, false);
loadRuns(false);
loadSessions(false);
pollStatus();
pollTimer = setInterval(pollStatus, 3000); pollTimer._ms = 3000;
</script>
""".encode("utf8")



class ThreadedHTTPServer(ThreadingHTTPServer):
    """The MJPEG stream holds its connection open. Without threading that one
    request would block every other page load and the whole dashboard would
    appear to hang."""
    daemon_threads = True
    allow_reuse_address = True


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, "application/json", json.dumps(obj).encode())

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE)
        if p == "/preview/stream":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = None
            idle = 0
            beat = 0.0
            try:
                while idle < 900:
                    try:
                        sig = os.path.getmtime(PREVIEW_JPG)
                    except OSError:
                        sig = None
                    if sig and (sig != last or (time.time() - beat) > 1.0):
                        try:
                            with open(PREVIEW_JPG, "rb") as fh:
                                data = fh.read()
                        except OSError:
                            data = b""
                        if data.endswith(b"\xff\xd9"):
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(
                                ("Content-Length: %d\r\n\r\n" % len(data)).encode())
                            self.wfile.write(data)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                            last = sig
                            beat = time.time()
                            idle = 0
                    idle += 1
                    time.sleep(0.08)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if p == "/live/stream":
            # MJPEG: hold the connection open and push frames as they appear.
            # Polling /live/frame every 2.5s looked like a slideshow; this
            # updates as fast as the detector produces annotated frames.
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last_sig = None
            idle = 0
            beat = 0.0
            try:
                while idle < 600:          # ~2 min of nothing -> close
                    shots = sorted(glob.glob(os.path.join(LIVEDIR, "*_latest.jpg")),
                                   key=os.path.getmtime)
                    if shots:
                        pth = shots[-1]
                        try:
                            sig = os.path.getmtime(pth)
                        except OSError:
                            sig = None
                        # A browser only paints a multipart frame once the NEXT
                        # boundary arrives. Detection runs at ~0.6 fps, so without
                        # a heartbeat re-send the newest frame stays invisible -
                        # and on a finished run it never appears at all.
                        resend = sig and (time.time() - beat) > 1.0
                        if sig and (sig != last_sig or resend):
                            try:
                                with open(pth, "rb") as fh:
                                    data = fh.read()
                            except OSError:
                                data = b""
                            if data.endswith(b"\xff\xd9"):
                                self.wfile.write(b"--frame\r\n")
                                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                                self.wfile.write(
                                    ("Content-Length: %d\r\n\r\n" % len(data)).encode())
                                self.wfile.write(data)
                                self.wfile.write(b"\r\n")
                                self.wfile.flush()
                                last_sig = sig
                                beat = time.time()
                                idle = 0
                    idle += 1
                    time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if p == "/live/frame":
            # newest annotated frame written by live_survey.py
            try:
                shots = sorted(glob.glob(os.path.join(LIVEDIR, "*_latest.jpg")),
                               key=os.path.getmtime)
            except OSError:
                shots = []
            if not shots:
                return self._send(404, "text/plain", b"no live frame")
            try:
                with open(shots[-1], "rb") as fh:
                    data = fh.read()
            except OSError:
                return self._send(404, "text/plain", b"unreadable")
            if not data.endswith(b"\xff\xd9"):
                return self._send(404, "text/plain", b"frame incomplete")
            return self._send(200, "image/jpeg", data)
        if p == "/api/status":
            return self._json(rig_status())
        if p == "/api/runs":
            return self._json([run_summary(r) for r in run_ids()])
        if p.startswith("/api/session/"):
            run = os.path.basename(p[len("/api/session/"):])
            if run not in run_ids():
                return self._send(404, "text/plain", b"no such run")
            return self._json(session_detail(run))
        if p.startswith("/aframe/"):
            parts = p[len("/aframe/"):].split("/")
            if len(parts) != 2:
                return self._send(404, "text/plain", b"bad frame path")
            run = os.path.basename(parts[0])
            try:
                i = int(parts[1])
            except ValueError:
                return self._send(404, "text/plain", b"bad index")
            al = annot_list(run)
            if not (0 <= i < len(al)):
                return self._send(404, "text/plain", b"out of range")
            try:
                with open(al[i], "rb") as fh:
                    return self._send(200, "image/jpeg", fh.read())
            except OSError:
                return self._send(404, "text/plain", b"unreadable")
        if p.startswith("/api/run/"):
            run = os.path.basename(p[len("/api/run/"):])
            if run not in run_ids():
                return self._send(404, "text/plain", b"no such run")
            return self._json(run_detail(run))
        if p.startswith("/frame/"):
            parts = p[len("/frame/"):].split("/")
            if len(parts) != 2:
                return self._send(404, "text/plain", b"bad frame path")
            run = os.path.basename(parts[0])
            try:
                i = int(parts[1])
            except ValueError:
                return self._send(404, "text/plain", b"bad index")
            fl = frame_list(run)
            if not (0 <= i < len(fl)):
                return self._send(404, "text/plain", b"out of range")
            try:
                with open(fl[i], "rb") as fh:
                    return self._send(200, "image/jpeg", fh.read())
            except OSError:
                return self._send(404, "text/plain", b"unreadable")
        self._send(404, "text/plain", b"not found")

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/control/preview_on":
            return self._json(preview_start())
        if p == "/api/control/preview_off":
            return self._json(preview_stop())
        if p not in ("/api/control/start", "/api/control/stop"):
            return self._send(404, "text/plain", b"not found")
        body = {}
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= 4096:
                body = json.loads(self.rfile.read(n).decode("utf8") or "{}")
            if not isinstance(body, dict):
                body = {}
        except (ValueError, OSError):
            body = {}
        if p.endswith("/start"):
            return self._json(start_lap(body))
        return self._json(stop_lap())


if __name__ == "__main__":
    print("survey dashboard on http://0.0.0.0:%d  (Ctrl-C to stop)" % PORT)
    print("  runs found: %s" % (", ".join(run_ids()) or "none"))
    ThreadedHTTPServer(("0.0.0.0", PORT), H).serve_forever()
