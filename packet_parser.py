"""
packet_parser.py
----------------
Parses IN-SPACe telemetry packets (Annexure 2: Telemetry Format) into
Telemetry objects. This is the ONLY accepted format:

 1  TEAM ID            2026 IN-SPACe-XXX
 2  TIME STAMPING      seconds since initial power
 3  PACKET COUNT       count of transmitted packets
 4  ALTITUDE           metres, relative to ground (0.1 m)
 5  PRESSURE           PASCAL (1 Pa)
 6  TEMP               deg C (0.1)
 7  VOLTAGE            volts (0.01)
 8  GNSS TIME          seconds
 9  GNSS LATITUDE      degrees (0.0001)
10  GNSS LONGITUDE     degrees (0.0001)
11  GNSS ALTITUDE      metres (0.1)
12  GNSS SATS          integer
13  ACCELEROMETER DATA x|y|z  (m/s2)
14  GYRO SPIN RATE     x|y|z  (deg/s)
15  FLIGHT SOFTWARE STATE   Boot, idle, launch detect, deploy, ...
16  OPTIONAL DATA      anything the mission wants (no commas)

Exactly 16 comma-separated fields, none empty.

Optional transport extras that are removed before parsing (the fields above
are unchanged by them):
 - a text label in front of the packet, e.g. "Raw Data: "
 - '$<packet>*HH' framing, HH = hex XOR checksum of <packet> (checked if present)
"""

import re

from telemetry_store import (
    Telemetry,
    Accelerometer,
    Gyroscope,
    Orientation,
)

from config import TEAM_ID, ALTITUDE_MIN_M

TELEMETRY_FIELDS = [
    "TEAM_ID",
    "TIME_STAMPING",
    "PACKET_COUNT",
    "ALTITUDE",
    "PRESSURE",
    "TEMP",
    "VOLTAGE",
    "GNSS_TIME",
    "GNSS_LATITUDE",
    "GNSS_LONGITUDE",
    "GNSS_ALTITUDE",
    "GNSS_SATS",
    "ACCELEROMETER_DATA",
    "GYRO_SPIN_RATE",
    "FLIGHT_SOFTWARE_STATE",
    "OPTIONAL_DATA",
]

FIELD_COUNT = len(TELEMETRY_FIELDS)   # 16

# Text labels the firmware may print in front of a packet, e.g. "Raw Data: ...".
_LABEL_PREFIX = re.compile(r"^\s*[A-Za-z][A-Za-z ]{0,24}:\s*")

# Flight software state is a free label (Boot, Idle, Launch Detect, Deploy, ...).
_STATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,31}$")

# Optional attitude inside OPTIONAL DATA: FIX=0|R=-1.0|P=-1.6|Y=-110.5
_RPY = re.compile(r"R=(-?[\d.]+)\|P=(-?[\d.]+)\|Y=(-?[\d.]+)")

MAX_PRESSURE_PA = 200000.0


class PacketParser:

    def __init__(self):
        pass

    # ---------------------------------------------------------

    def _safe_int(self, value: str, field: str) -> int:
        try:
            return int(float(value))
        except Exception:
            raise ValueError(f"Invalid integer in {field}")

    def _safe_float(self, value: str, field: str) -> float:
        try:
            return float(value)
        except Exception:
            raise ValueError(f"Invalid float in {field}")

    def _parse_vector(self, value: str, field: str):
        if "|" in value:
            parts = value.split("|")
        elif ":" in value:
            parts = value.split(":")
        else:
            raise ValueError(f"{field} must be x|y|z")

        if len(parts) != 3:
            raise ValueError(f"{field} must contain exactly X, Y and Z")

        try:
            return [float(p) for p in parts]
        except Exception:
            raise ValueError(f"Invalid number in {field}")

    # ---------------------------------------------------------

    def clean(self, line: str) -> str:
        """Removes a leading text label such as 'Raw Data:' from a received line."""
        return _LABEL_PREFIX.sub("", line.strip(), count=1).strip()

    def looks_like_telemetry(self, line: str) -> bool:
        """
        False for firmware debug/info text (e.g. 'GPS Coordinates:26.45, 80.19'),
        which must not be counted as a corrupt telemetry packet. A line counts as a
        telemetry attempt if it starts with our team ID or has packet-like length.
        """
        fields = line.lstrip("$").split(",")
        return len(fields) >= 10 or fields[0].strip() == TEAM_ID

    def _strip_framing(self, packet: str) -> str:
        """Optional '$<body>*HH' framing; a present-but-wrong checksum discards the packet."""
        if packet.startswith("$"):
            packet = packet[1:]

        star = packet.rfind("*")
        if star == -1:
            return packet

        body, cs = packet[:star], packet[star + 1:].strip()
        if len(cs) != 2 or any(c not in "0123456789abcdefABCDEF" for c in cs):
            raise ValueError("Malformed checksum")

        expected = 0
        for ch in body:
            expected ^= ord(ch)

        if expected != int(cs, 16):
            raise ValueError(
                f"Checksum mismatch (expected {expected:02X}, got {cs.upper()})"
            )

        return body

    # ---------------------------------------------------------

    def parse(self, packet: str) -> Telemetry:
        packet = self._strip_framing(self.clean(packet)).strip()

        if packet == "":
            raise ValueError("Empty packet")

        fields = [x.strip() for x in packet.split(",")]

        if len(fields) != FIELD_COUNT:
            raise ValueError(
                f"Expected {FIELD_COUNT} fields, received {len(fields)}"
            )

        for i, name in enumerate(TELEMETRY_FIELDS):
            if fields[i] == "":
                raise ValueError(f"Field {i + 1} ({name}) is empty")

        # Only our own team's packets are accepted (other teams may share the channel).
        if fields[0] != TEAM_ID:
            raise ValueError(
                f"Invalid TEAM ID : {fields[0]} (expected {TEAM_ID})"
            )

        state = fields[14].upper()
        if not _STATE_RE.match(state):
            raise ValueError(f"Invalid Flight State : {fields[14]}")

        accel = self._parse_vector(fields[12], "ACCELEROMETER DATA")
        gyro = self._parse_vector(fields[13], "GYRO SPIN RATE")

        try:
            gnss_time = str(self._safe_int(fields[7], "GNSS TIME"))
        except ValueError:
            gnss_time = fields[7]

        pressure_pa = self._safe_float(fields[4], "PRESSURE")

        telemetry = Telemetry(
            team_id=fields[0],
            timestamp=self._safe_int(fields[1], "TIME STAMPING"),
            packet_count=self._safe_int(fields[2], "PACKET COUNT"),
            altitude=self._safe_float(fields[3], "ALTITUDE"),
            # The format sends pascals; the dashboard and store work in hPa.
            pressure=round(pressure_pa / 100.0, 2),
            temperature=self._safe_float(fields[5], "TEMP"),
            voltage=self._safe_float(fields[6], "VOLTAGE"),
            gnss_time=gnss_time,
            gnss_latitude=self._safe_float(fields[8], "GNSS LATITUDE"),
            gnss_longitude=self._safe_float(fields[9], "GNSS LONGITUDE"),
            gnss_altitude=self._safe_float(fields[10], "GNSS ALTITUDE"),
            gnss_sats=self._safe_int(fields[11], "GNSS SATS"),
            accelerometer=Accelerometer(x=accel[0], y=accel[1], z=accel[2]),
            gyroscope=Gyroscope(x=gyro[0], y=gyro[1], z=gyro[2]),
            flight_state=state,
            optional_data=fields[15],
        )

        m = _RPY.search(telemetry.optional_data)
        if m:
            telemetry.orientation = Orientation(
                roll=float(m.group(1)), pitch=float(m.group(2)), yaw=float(m.group(3))
            )

        # -----------------------------------------------------
        # Basic Validation
        # -----------------------------------------------------

        if telemetry.packet_count < 0:
            raise ValueError("Invalid Packet Count")

        if telemetry.altitude < ALTITUDE_MIN_M:
            raise ValueError("Invalid Altitude")

        if pressure_pa < 0 or pressure_pa > MAX_PRESSURE_PA:
            raise ValueError("Pressure out of range (format expects pascals)")

        if telemetry.temperature < -100 or telemetry.temperature > 150:
            raise ValueError("Temperature Out of Range")

        if telemetry.voltage < 0:
            raise ValueError("Invalid Voltage")

        if telemetry.gnss_sats < 0 or telemetry.gnss_sats > 50:
            raise ValueError("Invalid GNSS Satellite Count")

        if telemetry.gnss_latitude < -90 or telemetry.gnss_latitude > 90:
            raise ValueError("Invalid Latitude")

        if telemetry.gnss_longitude < -180 or telemetry.gnss_longitude > 180:
            raise ValueError("Invalid Longitude")

        return telemetry


# ---------------------------------------------------------
# Global Parser Instance
# ---------------------------------------------------------

packet_parser = PacketParser()


if __name__ == "__main__":
    test_packet = (
        f"{TEAM_ID},121,118,-1126.4,99949,29.6,0.00,38458,26.4511,80.1907,102.3,6,"
        "0.02|0.00|0.00,0.00|0.00|0.00,IDLE,FIX=0|R=-1.0|P=-1.6|Y=-110.5"
    )

    try:
        result = packet_parser.parse(test_packet)
        print("PARSER TEST PASSED")
        print(result)
    except Exception as e:
        print("PARSER TEST FAILED")
        print(e)
