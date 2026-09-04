#!/usr/bin/env python3
"""
Live view of the survey WITHOUT touching the OAK-D.  (Claude, 2026-08-31)

record_survey.py owns the camera.  Only one process can.  So this serves the
newest JPEG the recorder has already written to disk, plus live RTK fix
quality parsed from /tmp/p1.log.

    ~/env/bin/python ~/survey_view.py        -> http://<pi>:8080
"""
import glob
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FRAMES = os.path.expanduser("~/oakd_project/data/frames")
P1LOG = "/tmp/p1.log"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

PAGE = b"""<!doctype html><meta charset=utf-8>
<title>Survey Live View</title>
<style>
 body{margin:0;background:#111;color:#eee;font:14px system-ui,sans-serif}
 header{padding:10px 14px;background:#1b1b1b;border-bottom:1px solid #333;
        display:flex;gap:18px;align-items:center;flex-wrap:wrap}
 b{font-weight:600}
 #img{display:block;max-width:100%;height:auto;margin:0 auto}
 .pill{padding:3px 9px;border-radius:99px;font-weight:600;font-size:12px}
 .fix{background:#0a5c2a;color:#7ef0a8}
 .float{background:#5c4a0a;color:#f0d97e}
 .dgps{background:#5c2f0a;color:#f0b27e}
 .bad{background:#5c0a0a;color:#f08d7e}
 .dim{color:#888}
</style>
<header>
  <span><b>run</b> <span id=run class=dim>...</span></span>
  <span><b>frames</b> <span id=n>0</span></span>
  <span><b>fix</b> <span id=fix class="pill dim">...</span></span>
  <span><b>corrections</b> <span id=corr class=dim>0 B</span></span>
  <span class=dim id=err></span>
</header>
<img id=img alt="waiting for frames">
<script>
const img=document.getElementById('img');
function stat(){
  fetch('/stat').then(r=>r.json()).then(d=>{
    document.getElementById('run').textContent=d.run||'none';
    document.getElementById('n').textContent=d.frames;
    document.getElementById('corr').textContent=d.corrections+' B';
    const f=document.getElementById('fix');
    f.textContent=d.fix;
    f.className='pill '+(d.fix.includes('RTKFixed')?'fix':
                         d.fix.includes('RTKFloat')?'float':
                         d.fix.includes('DGPS')?'dgps':'bad');
    document.getElementById('err').textContent=d.error||'';
  }).catch(e=>{});
}
function tick(){
  const u='/latest.jpg?t='+Date.now();
  const p=new Image();
  p.onload=()=>{img.src=u;setTimeout(tick,300);};
  p.onerror=()=>setTimeout(tick,1000);
  p.src=u;
}
tick(); stat(); setInterval(stat,1000);
</script>
"""


def newest_run():
    dirs = [d for d in glob.glob(os.path.join(FRAMES, "*")) if os.path.isdir(d)]
    return max(dirs, key=os.path.getmtime) if dirs else None


def newest_frame(run):
    """Newest fully-written frame.  The very newest may still be mid-write,
    so prefer the one before it when we have a choice."""
    if not run:
        return None, 0
    files = sorted(glob.glob(os.path.join(run, "f_*.jpg")))
    if not files:
        return None, 0
    return (files[-2] if len(files) > 1 else files[-1]), len(files)


def rtk_state():
    fix, corr = "no runner", 0
    try:
        with open(P1LOG, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 60000))
            tail = fh.read().decode("utf-8", "replace")
        m = re.findall(r"Type=([A-Za-z]+) \(\d\)", tail)
        if m:
            fix = m[-1]
        c = re.findall(r"corrections=(\d+) B", tail)
        if c:
            corr = int(c[-1])
    except FileNotFoundError:
        pass
    return fix, corr


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body, nocache=True):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if nocache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE)

        if path == "/latest.jpg":
            run = newest_run()
            f, _ = newest_frame(run)
            if not f:
                return self._send(404, "text/plain", b"no frames yet")
            try:
                with open(f, "rb") as fh:
                    data = fh.read()
            except OSError:
                return self._send(404, "text/plain", b"frame vanished")
            if not data.endswith(b"\xff\xd9"):
                return self._send(404, "text/plain", b"frame incomplete")
            return self._send(200, "image/jpeg", data)

        if path == "/stat":
            run = newest_run()
            _, n = newest_frame(run)
            fix, corr = rtk_state()
            body = json.dumps({
                "run": os.path.basename(run) if run else None,
                "frames": n,
                "fix": fix,
                "corrections": corr,
                "error": "" if n else "recorder not writing frames",
            }).encode()
            return self._send(200, "application/json", body)

        self._send(404, "text/plain", b"not found")


if __name__ == "__main__":
    print(f"survey view on http://0.0.0.0:{PORT}  (Ctrl-C to stop)")
    print("reads frames from disk - does NOT open the camera")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
