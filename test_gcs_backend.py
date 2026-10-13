"""
test_gcs_backend.py
-------------------
Automated unit and integration test suite for IN-SPACe Ground Control Station backend.
Run with: python test_gcs_backend.py
"""

import sys
import os
import unittest
import json

# Ensure project directory is in python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import app
from packet_parser import packet_parser
from telemetry_store import telemetry_store, Telemetry
from csv_logger import csv_logger
from serial_manager import serial_manager
from config import TEAM_ID


class GCSBackendTestCase(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

    # -------------------------------------------------------------
    # 1. Health & Ping Endpoints
    # -------------------------------------------------------------
    def test_01_health_check(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "OK")
        self.assertEqual(data["backend"], "Running")
        self.assertIn("rocket_connected", data)
        self.assertIn("cansat_connected", data)
        self.assertIn("uptime", data)

    def test_02_ping_check(self):
        response = self.client.get("/api/ping")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data["success"])
        self.assertEqual(data["response"], "pong")

    # -------------------------------------------------------------
    # 2. Available Ports Discovery
    # -------------------------------------------------------------
    def test_03_ports_discovery(self):
        response = self.client.get("/api/ports")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data["success"])
        self.assertIsInstance(data["ports"], list)

    # -------------------------------------------------------------
    # 3. Status Endpoint with Diagnostics
    # -------------------------------------------------------------
    def test_04_status_endpoint(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("rocket_connected", data)
        self.assertIn("cansat_connected", data)
        self.assertIn("logging", data)
        self.assertIn("uptime", data)
        self.assertIn("rocket", data)
        self.assertIn("cansat", data)
        self.assertIn("rx_packets", data["rocket"])
        self.assertIn("valid_packets", data["rocket"])
        self.assertIn("invalid_packets", data["rocket"])

    # -------------------------------------------------------------
    # 4. Telemetry Store Snapshot Endpoint
    # -------------------------------------------------------------
    def test_05_telemetry_endpoint(self):
        response = self.client.get("/api/telemetry")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("rocket", data)
        self.assertIn("cansat", data)
        self.assertIn("rocket_history", data)
        self.assertIn("cansat_history", data)
        self.assertIn("rocket_connected", data)
        self.assertIn("cansat_connected", data)

    # -------------------------------------------------------------
    # 5. Packet Parser Validation & Error Handling
    # -------------------------------------------------------------
    def test_06_valid_packet_parsing(self):
        valid_packet = (
            f"{TEAM_ID},"
            "1000,"
            "1,"
            "100.5,"
            "101320,"
            "25.4,"
            "7.4,"
            "123456,"
            "28.6139,"
            "77.2090,"
            "100.2,"
            "8,"
            "0.1|0.2|9.8,"
            "1.0|2.0|3.0,"
            "ASCENT,"
            "TEST"
        )
        telemetry = packet_parser.parse(valid_packet)
        self.assertIsInstance(telemetry, Telemetry)
        self.assertEqual(telemetry.team_id, TEAM_ID)
        self.assertEqual(telemetry.packet_count, 1)
        self.assertEqual(telemetry.altitude, 100.5)
        self.assertEqual(telemetry.pressure, 1013.2)   # 101320 Pa -> hPa
        self.assertEqual(telemetry.flight_state, "ASCENT")
        self.assertEqual(telemetry.accelerometer.z, 9.8)
        self.assertEqual(telemetry.gyroscope.x, 1.0)

    def test_07_valid_colon_imu_vector_packet(self):
        valid_packet_colon = (
            f"{TEAM_ID},"
            "1000,"
            "2,"
            "150.0,"
            "101000,"
            "24.0,"
            "7.2,"
            "123457,"
            "28.6140,"
            "77.2091,"
            "150.0,"
            "10,"
            "0.5:0.6:9.7,"
            "0.8:1.2:2.5,"
            "DESCENT,"
            "PARAM"
        )
        telemetry = packet_parser.parse(valid_packet_colon)
        self.assertEqual(telemetry.accelerometer.x, 0.5)
        self.assertEqual(telemetry.gyroscope.z, 2.5)

    def test_08_invalid_packet_field_count(self):
        short_packet = f"{TEAM_ID},1000,1,100.5"
        with self.assertRaises(ValueError) as ctx:
            packet_parser.parse(short_packet)
        self.assertIn("Expected 16 fields", str(ctx.exception))

    def test_09_invalid_packet_team_id(self):
        wrong_team_packet = (
            "INVALID-TEAM-ID,"
            "1000,1,100.5,101320,25.4,7.4,123456,28.6139,77.2090,100.2,8,"
            "0.1|0.2|9.8,1.0|2.0|3.0,ASCENT,TEST"
        )
        with self.assertRaises(ValueError) as ctx:
            packet_parser.parse(wrong_team_packet)
        self.assertIn("Invalid TEAM ID", str(ctx.exception))

    def test_10_invalid_packet_flight_state(self):
        bad_state_packet = (
            f"{TEAM_ID},"
            "1000,1,100.5,101320,25.4,7.4,123456,28.6139,77.2090,100.2,8,"
            "0.1|0.2|9.8,1.0|2.0|3.0,FLY!NG#HIGH,TEST"
        )
        with self.assertRaises(ValueError) as ctx:
            packet_parser.parse(bad_state_packet)
        self.assertIn("Invalid Flight State", str(ctx.exception))

    # -------------------------------------------------------------
    # 6. Disconnected Command Endpoints Safety
    # -------------------------------------------------------------
    def test_11_command_rocket_disconnected(self):
        serial_manager.rocket.connected = False
        response = self.client.post("/api/command/rocket", json={"command": "START_TELEMETRY"})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data["success"])
        self.assertIn("error", data)

    def test_12_command_cansat_disconnected(self):
        serial_manager.cansat.connected = False
        response = self.client.post("/api/command/cansat", json={"command": "START_TELEMETRY"})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data["success"])
        self.assertIn("error", data)

    # -------------------------------------------------------------
    # 7. CSV Logger Control
    # -------------------------------------------------------------
    def test_13_csv_logging_start_stop(self):
        response_start = self.client.post("/api/logging/start")
        self.assertEqual(response_start.status_code, 200)
        data_start = json.loads(response_start.data)
        self.assertTrue(data_start["success"])
        self.assertTrue(data_start["logging"])

        response_stop = self.client.post("/api/logging/stop")
        self.assertEqual(response_stop.status_code, 200)
        data_stop = json.loads(response_stop.data)
        self.assertTrue(data_stop["success"])
        self.assertFalse(data_stop["logging"])

    # -------------------------------------------------------------
    # 8. Official-format-only enforcement
    # -------------------------------------------------------------
    def test_14_real_rocket_packet(self):
        packet = (
            f"Raw Data: {TEAM_ID},121,118,-1126.4,99949,29.6,0.00,38458,26.4511,80.1907,"
            "102.3,6,0.02|0.00|0.00,0.00|0.00|0.00,IDLE,FIX=0|R=-1.0|P=-1.6|Y=-110.5"
        )
        t = packet_parser.parse(packet)
        self.assertEqual(t.team_id, TEAM_ID)
        self.assertEqual(t.timestamp, 121)
        self.assertEqual(t.packet_count, 118)
        self.assertEqual(t.altitude, -1126.4)
        self.assertEqual(t.pressure, 999.49)
        self.assertEqual(t.gnss_time, "38458")
        self.assertEqual(t.gnss_sats, 6)
        self.assertEqual(t.flight_state, "IDLE")
        self.assertEqual(t.orientation.yaw, -110.5)

    def test_15_old_26_field_format_rejected(self):
        old = (
            "888819,15000,105,120.50,1005.20,24.50,7.38,05:30:15,26.450200,80.332200,120.50,9,"
            "0.15,0.08,-1.25,0.50,0.30,0.10,1,0.00,0.00,12.50,420.00,12.40,-5.10,185.20"
        )
        with self.assertRaises(ValueError) as ctx:
            packet_parser.parse(old)
        self.assertIn("Expected 16 fields", str(ctx.exception))

    def test_16_other_team_rejected(self):
        other = (
            "2026 IN-SPACe-999,1,1,10.0,101320,25.0,7.4,100,28.6139,77.2090,10.0,8,"
            "0|0|9.8,0|0|0,IDLE,X"
        )
        with self.assertRaises(ValueError) as ctx:
            packet_parser.parse(other)
        self.assertIn("Invalid TEAM ID", str(ctx.exception))

    def test_17_empty_field_rejected(self):
        bad = f"{TEAM_ID},1,1,10.0,,25.0,7.4,100,28.6139,77.2090,10.0,8,0|0|9.8,0|0|0,IDLE,X"
        with self.assertRaises(ValueError) as ctx:
            packet_parser.parse(bad)
        self.assertIn("PRESSURE", str(ctx.exception))

    def test_18_free_form_flight_states_accepted(self):
        for state in ("Boot", "Idle", "Launch Detect", "Deploy", "LANDED"):
            p = f"{TEAM_ID},1,1,10.0,101320,25.0,7.4,100,28.6139,77.2090,10.0,8,0|0|9.8,0|0|0,{state},X"
            self.assertEqual(packet_parser.parse(p).flight_state, state.upper())


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING GCS BACKEND AUTOMATED TEST SUITE")
    print("=" * 60)
    unittest.main()
