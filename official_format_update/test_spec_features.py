"""Tests for: checksum framing, HTTP inject, command audit/ACK, session CSV, replay."""
import io, os, sys, time, unittest, json, threading
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import app
from packet_parser import packet_parser
from telemetry_store import telemetry_store
from csv_logger import csv_logger, COMMAND_AUDIT_PATH, CSV_PATH, CSV_HEADER
from config import TEAM_ID
import tempfile, pathlib
from serial_manager import serial_manager, SerialConnection

PKT = (f"{TEAM_ID},1000,1,100.5,101320,25.4,7.4,123456,28.6139,77.2090,100.2,8,"
       "0.1|0.2|9.8,1.0|2.0|3.0,ASCENT,TEST")

def framed(body):
    cs = 0
    for c in body: cs ^= ord(c)
    return f"${body}*{cs:02X}"

class SpecTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        telemetry_store.reset()

    def test_checksum_ok_and_bad(self):
        self.assertEqual(packet_parser.parse(framed(PKT)).packet_count, 1)
        with self.assertRaises(ValueError):
            packet_parser.parse(framed(PKT)[:-2] + "00" if not framed(PKT).endswith("00") else framed(PKT)[:-2] + "11")
        self.assertEqual(packet_parser.parse(PKT).packet_count, 1)  # unframed still works

    def test_inject_updates_store(self):
        r = self.client.post("/api/telemetry/inject", json={"device": "rocket", "packet": PKT})
        self.assertEqual(r.status_code, 200)
        snap = self.client.get("/api/telemetry").get_json()
        self.assertTrue(snap["rocket_connected"])
        self.assertEqual(snap["rocket"]["packet_count"], 1)
        self.assertTrue(snap["rocket"]["receive_time_utc"].endswith("+00:00"))
        bad = self.client.post("/api/telemetry/inject", json={"device": "rocket", "packet": "junk"})
        self.assertEqual(bad.status_code, 400)

    def test_official_csv_and_replay(self):
        # Log into a temp file so a real Flight_<TEAM_ID>.csv is never touched by tests.
        real_path = csv_logger.path
        tmp = pathlib.Path(tempfile.mkdtemp()) / CSV_PATH.name
        csv_logger.path = tmp
        try:
            self.assertEqual(CSV_PATH.name, f"Flight_{TEAM_ID}.csv")
            self.client.post("/api/logging/start")
            for n in (1, 2, 3):
                self.client.post("/api/telemetry/inject", json={
                    "device": "cansat", "packet": PKT.replace(",1000,1,100.5", f",{1000+n},{n},{100+n}.5", 1)})
            self.client.post("/api/logging/stop")
        finally:
            csv_logger.path = real_path

        text = tmp.read_text()
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], ",".join(CSV_HEADER))
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[1].split(",")[4], "101320")          # pressure logged in pascals
        self.assertEqual(len(CSV_HEADER), 18)                        # 16 official + DEVICE + RECEIVED_AT

        telemetry_store.reset()
        r = self.client.post("/api/replay/start", data={"file": (io.BytesIO(text.encode()), "s.csv"), "speed": "20"},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.data)
        for _ in range(50):
            if not self.client.get("/api/replay/status").get_json()["active"]: break
            time.sleep(0.1)
        st = self.client.get("/api/replay/status").get_json()
        self.assertEqual(st["sent"], 3)
        snap = self.client.get("/api/telemetry").get_json()
        self.assertEqual(snap["cansat"]["packet_count"], 3)
        self.assertEqual(snap["cansat"]["pressure"], 1013.2)         # back to hPa for the dashboard
        self.assertFalse(snap["replay_active"])
        self.assertFalse(snap["cansat_connected"])

    def test_replay_rejects_garbage(self):
        r = self.client.post("/api/replay/start", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_ack_and_audit(self):
        class FakeSerial:
            is_open = True
            def __init__(s): s.out = []
            def write(s, b): s.out.append(b)
        conn = serial_manager.rocket
        conn.serial, conn.connected = FakeSerial(), True
        threading.Timer(0.2, lambda: conn.ack_queue.put("ACK,DEPLOY")).start()
        before = COMMAND_AUDIT_PATH.read_text().count("\n") if COMMAND_AUDIT_PATH.exists() else 0
        r = self.client.post("/api/command/rocket", json={"command": "DEPLOY", "framed": True, "wait_ack": True}).get_json()
        self.assertTrue(r["success"]); self.assertEqual(r["ack"], "ACK")
        self.assertTrue(conn.serial.out[0].decode().startswith("$CMD,DEPLOY*"))
        self.assertEqual(COMMAND_AUDIT_PATH.read_text().count("\n"), before + (1 if before else 2))
        conn.connected = False

    # ---- real rocket firmware lines (seen in the field) ----
    REAL = (f"Raw Data: {TEAM_ID},121,118,-1126.4,99949,29.6,0.00,38458,26.4511,80.1907,"
            "102.3,6,0.02|0.00|0.00,0.00|0.00|0.00,IDLE,FIX=0|R=-1.0|P=-1.6|Y=-110.5")

    def test_real_firmware_packet(self):
        t = packet_parser.parse(self.REAL)
        self.assertEqual(t.team_id, TEAM_ID)
        self.assertEqual(t.packet_count, 118)
        self.assertEqual(t.flight_state, "IDLE")
        self.assertEqual(t.altitude, -1126.4)
        self.assertEqual(t.pressure, 999.49)            # Pa -> hPa
        self.assertEqual((t.orientation.roll, t.orientation.pitch, t.orientation.yaw), (-1.0, -1.6, -110.5))

    def test_debug_lines_are_not_telemetry(self):
        self.assertFalse(packet_parser.looks_like_telemetry(packet_parser.clean("GPS Coordinates:26.4511, 80.1907")))
        self.assertTrue(packet_parser.looks_like_telemetry(packet_parser.clean(self.REAL)))
        # a genuinely corrupt packet from our team is still reported
        self.assertTrue(packet_parser.looks_like_telemetry(f"{TEAM_ID},this,is,not,a,packet"))

if __name__ == "__main__":
    unittest.main()
