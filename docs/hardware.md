# Hardware notes

## Parts

| Part | Notes |
|---|---|
| Raspberry Pi 5 | main compute |
| Hailo-8 AI HAT+ (26 TOPS) | PCIe; needs `hailo_pci` in `/etc/modules` |
| OAK-D | used as a plain RGB camera (640×360) — no depth in this build |
| Quectel LG69T + Point One Polaris | RTK GNSS |
| VESC | motor controller, `/dev/ttyACM0` |
| Logitech F710 | manual override, `/dev/input/js0` |

## Traps that cost us real time

**The Pi's supply is 3 A, not 5 A.** Under load with the camera, GPS and VESC all
on the USB bus, it browns out. We saw the whole bus collapse mid-lap
(`error -71`, `error -19`) while `vcgencmd get_throttled` still reported `0x0`,
so the throttle flag is not a reliable indicator. Give the USB hub its own power
supply, and cap inference CPU usage (`--cores 2`).

**The GNSS receiver is one chip with two ports.** `if00` is the FusionEngine
control port and must belong to the correction runner; `if01` is NMEA for
DonkeyCar. Use `/dev/serial/by-id/` names — the `ttyUSBn` numbers swap on reboot.

**Never run two correction runners.** If a wedged runner survives a `SIGTERM`
and a second starts, both hold the control port, every reset request times out,
and the fix never converges. Two is strictly worse than none, because it cannot
recover on its own. `rtk_watchdog.sh` now waits for the old process to die,
escalates to `SIGKILL`, and refuses to start a duplicate.

**A blank fix name is not a lost fix.** The logger only stamps a fix quality on
loops where it parsed a GGA sentence (~1 Hz) while the drive loop runs far
faster, so most rows carry a position with a blank name — all of them still have
valid coordinates.

**Do not deploy a script while it is running.** `scp` truncates and rewrites in
place, and bash reads a script by byte offset as it executes: overwriting
`survey.sh` mid-lap made it resume in the middle of a token and skip its
shutdown stage. Ship to a temp name and `mv` — a rename leaves the running
process on the old inode.
