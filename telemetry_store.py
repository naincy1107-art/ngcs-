"""
telemetry_store.py
------------------
Central shared telemetry storage for the Ground Control Station.

Every module (serial manager, packet parser, Socket.IO, logger, etc.)
reads from or writes to this file.

Thread-safe.
"""

from dataclasses import dataclass, field, asdict
from collections import deque
from threading import Lock                          
from typing import Dict, Any
import time
from datetime import datetime, timezone

from config import MAX_HISTORY_POINTS


# ============================================================
# Sensor Models
# ============================================================

@dataclass
class Accelerometer:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Gyroscope:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Orientation:
    pitch: float = 0.0
    roll: float = 0.0
    yaw: float = 0.0


@dataclass
class GasSensors:
    ch4: float = 0.0
    co: float = 0.0
    nh3: float = 0.0
    co2: float = 0.0


# ============================================================
# Complete Telemetry Packet
# ============================================================

@dataclass
class Telemetry:

    # Identification
    team_id: str = ""
    timestamp: int = 0
    packet_count: int = 0

    # Environmental (BMP3XX / INA219)
    altitude: float = 0.0
    pressure: float = 0.0
    temperature: float = 0.0
    voltage: float = 0.0
    vertical_velocity: float = 0.0

    # GNSS
    gnss_time: str = ""
    gnss_latitude: float = 0.0
    gnss_longitude: float = 0.0
    gnss_altitude: float = 0.0
    gnss_sats: int = 0

    # IMU (BNO08x)
    accelerometer: Accelerometer = field(default_factory=Accelerometer)
    gyroscope: Gyroscope = field(default_factory=Gyroscope)
    orientation: Orientation = field(default_factory=Orientation)

    # Gas Sensors (MQ2 / MQ135)
    gas: GasSensors = field(default_factory=GasSensors)

    # Flight State
    flight_state: str = "--"
    optional_data: str = ""

    # Backend Metadata
    last_update: float = 0.0
    receive_time_utc: str = ""
    connected: bool = False


# =====================================================================
# TELEMETRY STORE
# =====================================================================

class TelemetryStore:

    def __init__(self):

        self.lock = Lock()

        self.rocket_history = deque(maxlen=MAX_HISTORY_POINTS)
        self.cansat_history = deque(maxlen=MAX_HISTORY_POINTS)

        self.reset()

    # ==============================================================
    # Reset Store
    # ==============================================================

    def reset(self):
        """
        Resets all telemetry attributes to clean unconnected zero values.
        Prevents ghost data or stale cached state on load.
        """
        lock_to_use = getattr(self, 'lock', None)
        if lock_to_use is None:
            lock_to_use = Lock()
            self.lock = lock_to_use

        with lock_to_use:
            self.rocket = Telemetry(
                team_id="",
                timestamp=0,
                packet_count=0,
                altitude=0.0,
                pressure=0.0,
                temperature=0.0,
                voltage=0.0,
                vertical_velocity=0.0,
                gnss_time="",
                gnss_latitude=0.0,
                gnss_longitude=0.0,
                gnss_altitude=0.0,
                gnss_sats=0,
                accelerometer=Accelerometer(),
                gyroscope=Gyroscope(),
                orientation=Orientation(),
                gas=GasSensors(),
                flight_state="--",
                optional_data="",
                last_update=0.0,
                connected=False,
            )

            self.cansat = Telemetry(
                team_id="",
                timestamp=0,
                packet_count=0,
                altitude=0.0,
                pressure=0.0,
                temperature=0.0,
                voltage=0.0,
                vertical_velocity=0.0,
                gnss_time="",
                gnss_latitude=0.0,
                gnss_longitude=0.0,
                gnss_altitude=0.0,
                gnss_sats=0,
                accelerometer=Accelerometer(),
                gyroscope=Gyroscope(),
                orientation=Orientation(),
                gas=GasSensors(),
                flight_state="--",
                optional_data="",
                last_update=0.0,
                connected=False,
            )

            if hasattr(self, 'rocket_history'):
                self.rocket_history.clear()
            if hasattr(self, 'cansat_history'):
                self.cansat_history.clear()

            self.rocket_connected = False
            self.cansat_connected = False

            self.latest_terrain = {
                "class_name": None,
                "confidence": 0.0,
                "probabilities": {},
                "last_update": 0.0,
                "available": False,
            }

            self.replay_active = False
            self.start_time = time.time()

    # ==============================================================
    # Derived values
    # ==============================================================

    @staticmethod
    def _derive_vertical_velocity(prev: Telemetry, new: Telemetry, now: float):
        """
        Legacy 16-field packets carry no vertical velocity. Derive it from
        successive altitudes (smoothed). 26-field packets (optional_data
        'VVEL:...') already carry the firmware's own value and are untouched.
        """
        if new.optional_data.startswith("VVEL:"):
            return
        if prev.packet_count <= 0 or prev.last_update <= 0:
            return
        dt = now - prev.last_update
        if not (0.05 <= dt <= 5.0):
            return
        raw = (new.altitude - prev.altitude) / dt
        new.vertical_velocity = round(0.5 * raw + 0.5 * prev.vertical_velocity, 2)

    # ==============================================================
    # Rocket
    # ==============================================================

    def update_rocket(self, telemetry: Telemetry):

        with self.lock:

            now = time.time()
            self._derive_vertical_velocity(self.rocket, telemetry, now)
            telemetry.last_update = now
            telemetry.receive_time_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            telemetry.connected = True

            self.rocket = telemetry
            self.rocket_connected = True

            self.rocket_history.append(
                {
                    "time": telemetry.last_update,
                    "altitude": telemetry.altitude,
                    "temperature": telemetry.temperature,
                    "pressure": telemetry.pressure,
                    "latitude": telemetry.gnss_latitude,
                    "longitude": telemetry.gnss_longitude,                
                }
            )

    # ==============================================================
    # CanSat
    # ==============================================================

    def update_cansat(self, telemetry: Telemetry):

        with self.lock:

            now = time.time()
            self._derive_vertical_velocity(self.cansat, telemetry, now)
            telemetry.last_update = now
            telemetry.receive_time_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            telemetry.connected = True

            self.cansat = telemetry
            self.cansat_connected = True

            self.cansat_history.append(
                {
                    "time": telemetry.last_update,
                    "altitude": telemetry.altitude,
                    "temperature": telemetry.temperature,
                    "pressure": telemetry.pressure,
                    "latitude": telemetry.gnss_latitude,
                    "longitude": telemetry.gnss_longitude,
                }
            )

    # ==============================================================
    # Get latest telemetry
    # ==============================================================

    def get_rocket(self) -> Dict[str, Any]:

        with self.lock:
            return asdict(self.rocket)

    def get_cansat(self) -> Dict[str, Any]:

        with self.lock:
            return asdict(self.cansat)

    # ==============================================================
    # History
    # ==============================================================

    def get_rocket_history(self):

        with self.lock:
            return list(self.rocket_history)

    def get_cansat_history(self):

        with self.lock:
            return list(self.cansat_history)

    # ==============================================================
    # Connection Status
    # ==============================================================

    def set_rocket_connection(self, status: bool):

        with self.lock:
            self.rocket_connected = status
            self.rocket.connected = status

    def set_cansat_connection(self, status: bool):

        with self.lock:
            self.cansat_connected = status
            self.cansat.connected = status

    # ==============================================================
    # ML - Terrain Classification
    # ==============================================================

    def update_terrain(self, prediction_data: Dict[str, Any]):
        """
        Stores the latest terrain classification result.
        """

        with self.lock:

            self.latest_terrain = {
                "class_name": prediction_data.get("class_name"),
                "confidence": prediction_data.get("confidence", 0.0),
                "probabilities": prediction_data.get("probabilities", {}),
                "last_update": time.time(),
                "available": True,
            }

    # --------------------------------------------------------------

    def get_terrain(self) -> Dict[str, Any]:

        with self.lock:
            return dict(self.latest_terrain)

    # ==============================================================
    # Dashboard Snapshot
    # ==============================================================

    def snapshot(self):

        with self.lock:

            return {
                "rocket": asdict(self.rocket),
                "cansat": asdict(self.cansat),
                "rocket_history": list(self.rocket_history),
                "cansat_history": list(self.cansat_history),
                "rocket_connected": self.rocket_connected,
                "cansat_connected": self.cansat_connected,
                "terrain": dict(self.latest_terrain),
                "replay_active": self.replay_active,
                "uptime": round(time.time() - self.start_time, 1),
            }


# =====================================================================
# Shared Instance
# =====================================================================

telemetry_store = TelemetryStore()