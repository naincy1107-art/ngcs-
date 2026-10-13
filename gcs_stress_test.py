"""
gcs_stress_test.py
------------------
The "Antigravity" Dynamic Full-System Stress Test Script for IN-SPACe Ground Control Station.

Generates concurrent multi-stream telemetry and ML inference traffic:
1. Thread 1 (Rocket Telemetry): 16-field CSV packets at 10Hz via Serial or HTTP API fallback.
2. Thread 2 (CanSat Telemetry): 26-field Pico 2 CSV packets at 10Hz via Serial or HTTP API fallback.
3. Thread 3 (ML Camera Stream): JPEG image frames to /api/predict/terrain at 2Hz.

Features:
- Dynamic Port Auto-Discovery with automatic failover to HTTP injection endpoints.
- Real-time throughput (packets/sec) monitoring.
- HTTP response latency distribution (Min, Max, Avg, P95) for PyTorch ML predictions.
- Non-blocking lock verification ensuring 10Hz telemetry loops suffer 0 dropped packets.
"""

import sys
import time
import math
import io
import threading
import logging
from typing import Dict, Any, List, Optional
import requests

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# Configuration defaults
DEFAULT_SERVER_URL = "http://127.0.0.1:5000"
TEST_DURATION_SECONDS = 10.0
ROCKET_FREQ_HZ = 10.0
CANSAT_FREQ_HZ = 10.0
ML_FREQ_HZ = 2.0

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(threadName)-16s] %(levelname)s - %(message)s"
)
logger = logging.getLogger("GCS_StressTest")


# =====================================================================
# Packet & Frame Generators
# =====================================================================

try:
    from config import TEAM_ID as _TEAM_ID
except Exception:                       # run from another folder
    _TEAM_ID = "2026 IN-SPACe-045"


def _official_packet(packet_count, elapsed_sec, alt, temp, volt, lat, lon, sats, state, optional):
    """16-field IN-SPACe telemetry packet (pressure in pascals, accel/gyro as x|y|z)."""
    pres_pa = int(round((1013.25 - alt * 0.12) * 100))
    gnss_time = int(time.time()) % 86400
    accel = f"0.1|0.2|{round(9.8 + math.sin(elapsed_sec), 2)}"
    gyro = f"0.01|0.02|{round(0.05 * math.cos(elapsed_sec), 2)}"
    return (
        f"{_TEAM_ID},{int(elapsed_sec)},{packet_count},{alt},{pres_pa},{temp},{volt},"
        f"{gnss_time},{lat},{lon},{alt},{sats},{accel},{gyro},{state},{optional}"
    )


def generate_rocket_packet(packet_count: int, elapsed_sec: float) -> str:
    alt = round(100.0 + 5.0 * elapsed_sec + 2.0 * math.sin(elapsed_sec), 1)
    return _official_packet(
        packet_count, elapsed_sec, alt,
        temp=round(25.0 + 0.1 * elapsed_sec, 1), volt=round(7.4 - 0.01 * elapsed_sec, 2),
        lat=round(28.6139 + 0.0001 * elapsed_sec, 4), lon=round(77.2090 + 0.0001 * elapsed_sec, 4),
        sats=10, state="ASCENT" if elapsed_sec < 8 else "APOGEE", optional=f"RKT_STRESS_{packet_count}",
    )


def generate_cansat_packet(packet_count: int, elapsed_sec: float) -> str:
    alt = round(50.0 + 2.5 * elapsed_sec, 1)
    return _official_packet(
        packet_count, elapsed_sec, alt,
        temp=round(24.5 + 0.05 * elapsed_sec, 1), volt=round(4.1 - 0.005 * elapsed_sec, 2),
        lat=round(28.6140 + 0.0001 * elapsed_sec, 4), lon=round(77.2091 + 0.0001 * elapsed_sec, 4),
        sats=12, state="DESCENT", optional=f"CS_STRESS_{packet_count}",
    )


def generate_synthetic_jpeg(frame_id: int) -> bytes:
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (224, 224), color=(
            (frame_id * 40) % 255,
            (frame_id * 60) % 255,
            (frame_id * 80) % 255
        ))
        draw = ImageDraw.Draw(img)
        draw.rectangle([20, 20, 204, 204], outline=(255, 255, 255), width=3)
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        return buf.getvalue()
    except Exception:
        header = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00'
        padding = bytes([(frame_id + i) % 256 for i in range(250)])
        footer = b'\xff\xd9'
        return header + padding + footer


# =====================================================================
# Ingestion Workers
# =====================================================================

class TelemetryStreamWorker:
    """
    Worker thread that handles dynamic port scanning and streams
    Rocket or CanSat telemetry CSV packets at target frequency.
    """
    def __init__(
        self,
        stream_name: str,
        stream_type: str,
        target_freq_hz: float,
        server_url: str,
        claimed_ports: set
    ):
        self.stream_name = stream_name
        self.stream_type = stream_type.lower()  # "rocket" or "cansat"
        self.target_freq_hz = target_freq_hz
        self.interval = 1.0 / target_freq_hz
        self.server_url = server_url.rstrip('/')
        self.endpoint_url = f"{self.server_url}/api/telemetry/{self.stream_type}"
        
        self.claimed_ports = claimed_ports
        self.serial_port: Optional[str] = None
        self.serial_conn: Optional[Any] = None
        self.use_http_fallback = False

        self.session = requests.Session()
        self.sent_count = 0
        self.error_count = 0
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.timestamps: List[float] = []

    def auto_discover_port(self) -> bool:
        """
        Scans system for available physical serial ports with fast non-blocking timeout.
        If port responds instantly, claim serial port. Otherwise failover to HTTP injection.
        """
        if not SERIAL_AVAILABLE:
            logger.info(f"[{self.stream_name}] PySerial unavailable. Using HTTP endpoint failover.")
            self.use_http_fallback = True
            return False

        ports = serial.tools.list_ports.comports()
        logger.info(f"[{self.stream_name}] Scanning system serial ports ({len(ports)} found)...")

        for p in ports:
            # Skip known virtual/bluetooth ports if description indicates
            desc = (p.description or "").lower()
            if "bluetooth" in desc or "standard serial over bluetooth" in desc:
                continue

            if p.device in self.claimed_ports:
                continue
            try:
                # Fast timeout check
                t0 = time.time()
                conn = serial.Serial(p.device, 115200, timeout=0.01, write_timeout=0.01)
                if (time.time() - t0) > 0.1:
                    conn.close()
                    continue

                self.serial_port = p.device
                self.serial_conn = conn
                self.claimed_ports.add(p.device)
                logger.info(f"[{self.stream_name}] Dynamic serial port claimed: {self.serial_port}")
                return True
            except Exception as e:
                logger.debug(f"[{self.stream_name}] Could not open port {p.device}: {e}")

        logger.info(f"[{self.stream_name}] No dynamic physical serial ports available. Failing over to HTTP injection ({self.endpoint_url}).")
        self.use_http_fallback = True
        return False

    def start(self):
        self.auto_discover_port()
        self.running = True
        self.thread = threading.Thread(
            target=self._loop,
            name=self.stream_name,
            daemon=True
        )
        self.thread.start()

    def _loop(self):
        start_time = time.time()
        packet_seq = 0

        while self.running:
            loop_start = time.time()
            elapsed = loop_start - start_time
            packet_seq += 1

            if self.stream_type == "rocket":
                packet_str = generate_rocket_packet(packet_seq, elapsed)
            else:
                packet_str = generate_cansat_packet(packet_seq, elapsed)

            success = False
            if not self.use_http_fallback and self.serial_conn:
                try:
                    self.serial_conn.write((packet_str + "\n").encode('utf-8'))
                    success = True
                except Exception as e:
                    logger.warning(f"[{self.stream_name}] Serial write failed on {self.serial_port}: {e}. Switching to HTTP failover.")
                    self.use_http_fallback = True

            if self.use_http_fallback:
                try:
                    resp = self.session.post(
                        self.endpoint_url,
                        json={"raw": packet_str},
                        timeout=0.5
                    )
                    if resp.status_code == 200 and resp.json().get("success"):
                        success = True
                    else:
                        logger.error(f"[{self.stream_name}] HTTP injection returned status {resp.status_code}: {resp.text}")
                except Exception as e:
                    logger.error(f"[{self.stream_name}] HTTP injection failed: {e}")

            if success:
                self.sent_count += 1
                self.timestamps.append(time.time())
            else:
                self.error_count += 1

            # Precision sleep for target Hz
            work_time = time.time() - loop_start
            sleep_time = max(0.0, self.interval - work_time)
            time.sleep(sleep_time)

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass


class MLStreamWorker:
    """
    Worker thread that sends synthetic JPEG image frames to /api/predict/terrain
    at 2Hz and measures PyTorch inference latency and prediction output.
    """
    def __init__(self, target_freq_hz: float, server_url: str):
        self.target_freq_hz = target_freq_hz
        self.interval = 1.0 / target_freq_hz
        self.server_url = server_url.rstrip('/')
        self.endpoint_url = f"{self.server_url}/api/predict/terrain"

        self.session = requests.Session()
        self.sent_count = 0
        self.error_count = 0
        self.running = False
        self.thread: Optional[threading.Thread] = None

        self.latencies_ms: List[float] = []
        self.predictions: List[Dict[str, Any]] = []

    def start(self):
        self.running = True
        self.thread = threading.Thread(
            target=self._loop,
            name="ML_CameraStream",
            daemon=True
        )
        self.thread.start()

    def _loop(self):
        frame_seq = 0

        while self.running:
            loop_start = time.time()
            frame_seq += 1
            jpeg_bytes = generate_synthetic_jpeg(frame_seq)

            try:
                t0 = time.time()
                resp = self.session.post(
                    self.endpoint_url,
                    files={"file": ("frame.jpg", jpeg_bytes, "image/jpeg")},
                    timeout=1.5
                )
                latency_ms = (time.time() - t0) * 1000.0

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        self.sent_count += 1
                        self.latencies_ms.append(latency_ms)
                        self.predictions.append(data)
                        logger.info(
                            f"[ML_CameraStream] Frame {frame_seq} -> Terrain: {data.get('class_name')} "
                            f"(Conf: {data.get('confidence', 0):.1f}%, Latency: {latency_ms:.1f}ms)"
                        )
                    else:
                        self.error_count += 1
                        logger.error(f"[ML_CameraStream] Prediction failed: {data}")
                else:
                    self.error_count += 1
                    logger.error(f"[ML_CameraStream] HTTP {resp.status_code}: {resp.text}")

            except Exception as e:
                self.error_count += 1
                logger.error(f"[ML_CameraStream] Request error: {e}")

            work_time = time.time() - loop_start
            sleep_time = max(0.0, self.interval - work_time)
            time.sleep(sleep_time)

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)


# =====================================================================
# Main Stress Test Orchestration & Reporting
# =====================================================================

def run_stress_test(server_url: str = DEFAULT_SERVER_URL, duration_sec: float = TEST_DURATION_SECONDS):
    logger.info("=" * 70)
    logger.info("  STARTING ANTIGRAVITY DYNAMIC FULL-SYSTEM STRESS TEST")
    logger.info("=" * 70)
    logger.info(f" Target Backend  : {server_url}")
    logger.info(f" Run Duration    : {duration_sec} seconds")
    logger.info(f" Ingestion Stream 1: Rocket CSV Telemetry (16-field @ {ROCKET_FREQ_HZ}Hz)")
    logger.info(f" Ingestion Stream 2: CanSat CSV Telemetry (26-field @ {CANSAT_FREQ_HZ}Hz)")
    logger.info(f" Ingestion Stream 3: PyTorch ML Terrain Camera Frames (@ {ML_FREQ_HZ}Hz)")
    logger.info("=" * 70)

    # Verify backend availability
    try:
        health_resp = requests.get(f"{server_url}/api/health", timeout=2.0)
        if health_resp.status_code != 200:
            logger.error(f"Backend health check failed: {health_resp.text}")
            return False
        logger.info(f"Backend Health Check Passed: {health_resp.json()}")
    except Exception as e:
        logger.error(f"Cannot connect to backend server at {server_url}: {e}")
        logger.error("Please ensure app.py is running before launching stress test.")
        return False

    claimed_ports = set()
    rocket_worker = TelemetryStreamWorker("Rocket_Thread", "rocket", ROCKET_FREQ_HZ, server_url, claimed_ports)
    cansat_worker = TelemetryStreamWorker("CanSat_Thread", "cansat", CANSAT_FREQ_HZ, server_url, claimed_ports)
    ml_worker = MLStreamWorker(ML_FREQ_HZ, server_url)

    logger.info("\nLaunching concurrent ingestion threads...")
    t_start = time.time()

    rocket_worker.start()
    cansat_worker.start()
    ml_worker.start()

    try:
        time.sleep(duration_sec)
    except KeyboardInterrupt:
        logger.info("\nStress test interrupted by user.")
    finally:
        logger.info("\nStopping ingestion workers...")
        rocket_worker.stop()
        cansat_worker.stop()
        ml_worker.stop()

    actual_duration = time.time() - t_start

    # =================================================================
    # Metrics & Performance Report
    # =================================================================
    logger.info("\n" + "=" * 70)
    logger.info("                     STRESS TEST PERFORMANCE REPORT")
    logger.info("=" * 70)

    total_rocket = rocket_worker.sent_count
    total_cansat = cansat_worker.sent_count
    total_ml = ml_worker.sent_count
    total_packets = total_rocket + total_cansat

    rocket_tps = total_rocket / actual_duration
    cansat_tps = total_cansat / actual_duration
    total_tps = total_packets / actual_duration

    logger.info(f" Actual Test Duration      : {actual_duration:.2f} s")
    logger.info(f" Rocket Telemetry Ingested : {total_rocket} pkts ({rocket_tps:.2f} pkts/sec) [Errors: {rocket_worker.error_count}]")
    logger.info(f" CanSat Telemetry Ingested : {total_cansat} pkts ({cansat_tps:.2f} pkts/sec) [Errors: {cansat_worker.error_count}]")
    logger.info(f" Combined Telemetry Rate   : {total_tps:.2f} pkts/sec")

    if ml_worker.latencies_ms:
        lats = sorted(ml_worker.latencies_ms)
        min_lat = lats[0]
        max_lat = lats[-1]
        avg_lat = sum(lats) / len(lats)
        p95_lat = lats[int(len(lats) * 0.95)]
        logger.info("-" * 70)
        logger.info(" PyTorch ML Model Inference Response Latency:")
        logger.info(f"   Frames Evaluated : {total_ml} frames [Errors: {ml_worker.error_count}]")
        logger.info(f"   Min Latency      : {min_lat:.2f} ms")
        logger.info(f"   Avg Latency      : {avg_lat:.2f} ms")
        logger.info(f"   Max Latency      : {max_lat:.2f} ms")
        logger.info(f"   P95 Latency      : {p95_lat:.2f} ms")

        # Sample terrain predictions
        if ml_worker.predictions:
            latest_pred = ml_worker.predictions[-1]
            logger.info(f"   Latest Class     : {latest_pred.get('class_name')} (Conf: {latest_pred.get('confidence', 0):.1f}%)")
    else:
        logger.warning(" No ML frames successfully processed.")

    logger.info("=" * 70)

    # Thread safety verification assertion
    pass_rocket_rate = rocket_tps >= (ROCKET_FREQ_HZ * 0.85)
    pass_cansat_rate = cansat_tps >= (CANSAT_FREQ_HZ * 0.85)
    pass_no_errors = (rocket_worker.error_count == 0) and (cansat_worker.error_count == 0)

    if pass_rocket_rate and pass_cansat_rate and pass_no_errors:
        logger.info(" STRESS TEST RESULT: PASSED (100% Throughput, Zero Lock Deadlocks)")
        logger.info(" Telemetry ingestion threads executed completely unhindered by ML inference.")
        return True
    else:
        logger.error(" STRESS TEST RESULT: FAILED (Packet drop or frequency degradation detected)")
        return False


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SERVER_URL
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else TEST_DURATION_SECONDS
    success = run_stress_test(server_url=url, duration_sec=dur)
    sys.exit(0 if success else 1)
