#!/usr/bin/env python3
"""Set the DonkeyCar drive mode over the SAME channel the web UI uses.

    ~/env/bin/python ~/set_drive_mode.py local     # Full Auto (path following)
    ~/env/bin/python ~/set_drive_mode.py user      # manual / stop driving

WHY NOT `POST /drive`: that handler sets only `application.mode`. The vehicle
loop then calls LocalWebController.run_threaded(mode=<user/mode from memory>),
which does:

    if mode is not None and self.mode != mode:
        self.mode = mode            # <- reverts to whatever the joystick emitted
    if self.mode_latch is not None:
        self.mode = self.mode_latch # <- only a LATCHED mode survives

With `--js` the joystick re-emits 'user' every loop, so a POST is undone within
~50 ms. The /wsDrive handler sets `mode_latch` as well, which is why the real
web UI works. Exit 0 only when the mode is actually latched.
"""
import sys, json
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

MODE = sys.argv[1] if len(sys.argv) > 1 else "user"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8887
URL = "ws://127.0.0.1:%d/wsDrive" % PORT


async def main():
    conn = await websocket_connect(URL, connect_timeout=5)
    conn.write_message(json.dumps({
        "angle": 0.0, "throttle": 0.0,
        "drive_mode": MODE, "recording": False}))
    # Give the vehicle loop a few cycles to consume the latch before closing;
    # dropping the socket immediately can race the read.
    await IOLoop.current().run_in_executor(None, __import__("time").sleep, 1.0)
    conn.close()
    print("drive mode -> %s" % MODE)


try:
    IOLoop.current().run_sync(main, timeout=15)
except Exception as e:
    print("could not set drive mode: %s: %s" % (type(e).__name__, e))
    sys.exit(1)
