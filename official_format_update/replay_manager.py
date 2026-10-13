"""
replay_manager.py
-----------------
Replays a previously logged flight-session CSV through the exact same
TelemetryStore -> Socket.IO broadcast pipeline as live serial data.
"""

import csv
import io
import threading
from datetime import datetime

from telemetry_store import telemetry_store
from packet_parser import packet_parser, TELEMETRY_FIELDS

REQUIRED_COLUMNS = set(TELEMETRY_FIELDS) | {"DEVICE"}
MAX_STEP_DELAY = 2.0   # seconds; caps long gaps in the original log


def _ts(row):
    try:
        return datetime.strptime(row["RECEIVED_AT"].rstrip("Z"), "%Y-%m-%d %H:%M:%S.%f")
    except Exception:
        return None


def parse_session_csv(text: str):
    """
    Returns [(device, Telemetry, received_at|None), ...].
    Every row is rebuilt into an official packet and run through the same parser as
    live data, so a replay can never show anything the live path would reject.
    """
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
        raise ValueError(
            "Not a Flight_<TEAM_ID>.csv log in the official format "
            "(expected the 16 telemetry columns plus DEVICE)."
        )

    frames = []
    for row in reader:
        device = (row.get("DEVICE") or "").strip().lower()
        if device not in ("rocket", "cansat"):
            continue

        packet = ",".join((row.get(name) or "") for name in TELEMETRY_FIELDS)
        try:
            telemetry = packet_parser.parse(packet)
        except ValueError:
            continue

        frames.append((device, telemetry, _ts(row)))

    if not frames:
        raise ValueError("Log contains no valid rocket/cansat rows.")

    return frames


class ReplayManager:

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._state = {"active": False, "total": 0, "sent": 0, "speed": 1.0, "loop": False}

    # ----------------------------------------------------

    def start(self, csv_text: str, speed: float = 1.0, loop: bool = False):
        # Imported here to avoid a circular import at module load.
        from serial_manager import serial_manager

        if serial_manager.rocket.is_connected() or serial_manager.cansat.is_connected():
            raise RuntimeError("Disconnect live serial links before starting a replay.")

        frames = parse_session_csv(csv_text)
        speed = max(0.1, min(float(speed), 20.0))

        self.stop()

        with self._lock:
            self._stop.clear()
            telemetry_store.reset()
            telemetry_store.replay_active = True
            self._state = {"active": True, "total": len(frames), "sent": 0,
                           "speed": speed, "loop": loop}

            self._thread = threading.Thread(
                target=self._run, args=(frames, speed, loop),
                daemon=True, name="ReplayFeeder",
            )
            self._thread.start()

        return {"total_packets": len(frames), "speed": speed, "loop": loop}

    # ----------------------------------------------------

    def _run(self, frames, speed, loop):
        try:
            while True:
                prev = None
                for device, telemetry, received in frames:
                    if self._stop.is_set():
                        return

                    if prev is not None and received is not None:
                        gap = (received - prev).total_seconds()
                        delay = min(max(gap, 0.0), MAX_STEP_DELAY) / speed
                    else:
                        delay = 0.1 / speed
                    prev = received or prev

                    if self._stop.wait(delay):
                        return

                    if device == "rocket":
                        telemetry_store.update_rocket(telemetry)
                    else:
                        telemetry_store.update_cansat(telemetry)

                    with self._lock:
                        self._state["sent"] += 1

                if not loop:
                    return
        finally:
            self._finish()

    def _finish(self):
        with self._lock:
            self._state["active"] = False
        telemetry_store.replay_active = False
        # Zero-assumption: replayed data must not look like a live link.
        telemetry_store.set_rocket_connection(False)
        telemetry_store.set_cansat_connection(False)

    # ----------------------------------------------------

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3)

    def status(self):
        with self._lock:
            return dict(self._state)


replay_manager = ReplayManager()
