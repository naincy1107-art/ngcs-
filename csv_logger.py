"""
csv_logger.py
-------------
Writes Rocket and CanSat telemetry into one CSV file in the official
IN-SPACe telemetry format (16 fields) plus DEVICE and RECEIVED_AT.

Output:
logs/csv/Flight_<TEAM_ID>.csv   (path derived from config.py)
"""

import csv
import threading
from datetime import datetime, timezone

from telemetry_store import Telemetry
from packet_parser import TELEMETRY_FIELDS

from config import (
    LOG_DIRECTORY,
    CSV_DIRECTORY,
    CSV_FILE_PREFIX,
    CSV_FILE_EXTENSION,
    CSV_TEAM_NAME,
)

# ============================================================
# Configuration
# ============================================================

# Telemetry log: logs/csv/Flight_<TEAM_ID>.csv (name per Annexure 2). One file,
# new rows are appended. config.py already creates CSV_DIRECTORY on import.

CSV_PATH = CSV_DIRECTORY / f"{CSV_FILE_PREFIX}_{CSV_TEAM_NAME}{CSV_FILE_EXTENSION}"

# Columns: the 16 official telemetry fields in order, then two GCS-only extras.
CSV_HEADER = list(TELEMETRY_FIELDS) + ["DEVICE", "RECEIVED_AT"]

COMMAND_AUDIT_PATH = LOG_DIRECTORY / "command_audit.csv"


# ============================================================
# CSV Logger
# ============================================================

class CSVLogger:

    def __init__(self):

        self.lock = threading.Lock()

        self.file = None
        self.writer = None

        self.logging = False
        self.path = CSV_PATH

        self._audit_lock = threading.Lock()

    # --------------------------------------------------------

    def _open_unlocked(self):
        """
        Actual file-opening logic. Must only be called while
        holding self.lock.
        """

        if self.file is not None:
            # Already open -- avoid leaking a duplicate file handle
            # if start_logging()/open() is ever called twice by mistake.
            self.logging = True
            return

        # An existing file with a different header (older GCS version) must not be
        # mixed with the official format: move it aside, keep the data.
        if self.path.exists() and self.path.stat().st_size > 0:
            with open(self.path, "r", encoding="utf-8", newline="") as old:
                first_line = old.readline().strip()
            if first_line != ",".join(CSV_HEADER):
                backup = self.path.with_name(
                    self.path.stem + "_old_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                    + self.path.suffix
                )
                self.path.rename(backup)

        file_exists = self.path.exists()

        self.file = open(
            self.path,
            "a",
            newline="",
            encoding="utf-8"
        )

        self.writer = csv.writer(self.file)

        if not file_exists:
            self.writer.writerow(CSV_HEADER)
            self.file.flush()

        self.logging = True

    # --------------------------------------------------------

    def open(self):

        with self.lock:
            self._open_unlocked()

    # --------------------------------------------------------

    def close(self):

        with self.lock:

            self.logging = False

            if self.file:

                self.file.flush()
                self.file.close()

                self.file = None
                self.writer = None

    # --------------------------------------------------------

    def log_packet(
        self,
        device: str,
        telemetry: Telemetry
    ):
        """
        Logs one telemetry packet.

        The logging flag and writer are both checked *inside* the
        lock so a concurrent close()/open() from another thread
        (e.g. a "Stop Logging" button) can never leave this call
        holding a stale/None writer.
        """

        with self.lock:

            if not self.logging or self.writer is None:
                return

            t = telemetry
            self.writer.writerow([
                t.team_id,
                t.timestamp,
                t.packet_count,
                t.altitude,
                int(round(t.pressure * 100)),          # stored in hPa, logged in pascals
                t.temperature,
                t.voltage,
                t.gnss_time,
                t.gnss_latitude,
                t.gnss_longitude,
                t.gnss_altitude,
                t.gnss_sats,
                f"{t.accelerometer.x}|{t.accelerometer.y}|{t.accelerometer.z}",
                f"{t.gyroscope.x}|{t.gyroscope.y}|{t.gyroscope.z}",
                t.flight_state,
                t.optional_data,

                device.upper(),
                datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                )[:-3] + "Z",
            ])

            self.file.flush()

    # --------------------------------------------------------

    def start_logging(self):

        with self.lock:

            if self.logging:
                return

            self._open_unlocked()

    # --------------------------------------------------------

    def stop_logging(self):

        self.close()

    # --------------------------------------------------------

    def audit_command(self, device: str, command: str, success: bool, ack: str = ""):
        """
        Appends one uplink command to logs/command_audit.csv with a
        millisecond UTC timestamp.
        """
        with self._audit_lock:
            new_file = not COMMAND_AUDIT_PATH.exists()
            with open(COMMAND_AUDIT_PATH, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(["TIMESTAMP_UTC", "DEVICE", "COMMAND", "SENT", "ACK"])
                w.writerow([
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z",
                    device.upper(),
                    command.strip(),
                    "YES" if success else "NO",
                    ack,
                ])

    # --------------------------------------------------------

    def is_logging(self):

        with self.lock:
            return self.logging


# ============================================================
# Global Logger
# ============================================================

csv_logger = CSVLogger()


# ============================================================
# Standalone Test
# ============================================================

if __name__ == "__main__":

    print("CSV Logger Ready")
    print(f"Logging File : {CSV_PATH}")