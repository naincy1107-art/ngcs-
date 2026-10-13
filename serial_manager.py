"""
serial_manager.py
-----------------
Handles serial communication with Rocket and CanSat.

Features:
- Connect/Disconnect
- Continuous reading using background threads
- Auto reconnect
- Send commands
- Packet parsing
- Update telemetry store
"""
import queue
import threading
import time
import logging
import serial
import serial.tools.list_ports
from csv_logger import csv_logger

from config import (
    DEFAULT_BAUDRATE,
    SERIAL_TIMEOUT,
    AUTO_RECONNECT,
    RECONNECT_INTERVAL,
    COMMAND_TIMEOUT,
)

from packet_parser import packet_parser
from telemetry_store import telemetry_store


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)


class SerialConnection:
    """
    Represents one serial device.

    Example:
        Rocket XBee
        CanSat XBee
    """

    def __init__(self, name: str):

        self.name = name
        self.port = None
        self.baudrate = DEFAULT_BAUDRATE
        self.serial = None
        self.connected = False
        self.read_thread = None
        self.stop_event = threading.Event()
        self.ack_queue = queue.Queue()
        self._buf = b""
        self.line_callback = None   # fn(device, line, ok, error) -> None

        # Diagnostics
        self.rx_packets = 0
        self.valid_packets = 0
        self.invalid_packets = 0
        self.last_rx_time = None
        self.last_error = None

    def get_diagnostics(self):
        return {
            "rx_packets": self.rx_packets,
            "valid_packets": self.valid_packets,
            "invalid_packets": self.invalid_packets,
            "last_rx_time": self.last_rx_time,
            "last_error": self.last_error,
        }

    # --------------------------------------------------------

    def connect(self, port: str, baudrate: int = DEFAULT_BAUDRATE):

        if self.connected:
            if self.port == port and self.baudrate == baudrate:
                return True
            # Port or baudrate changed, disconnect first
            self.disconnect()

        self.port = port
        self.baudrate = baudrate

        try:

            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=SERIAL_TIMEOUT,
            )

            self.connected = True
            self._buf = b""

            self.stop_event.clear()

            self.read_thread = threading.Thread(
                target=self.read_loop,
                daemon=True,
            )

            self.read_thread.start()

            logging.info(f"{self.name} connected on {self.port} at {self.baudrate} baud")

            return True

        except Exception as e:

            self.last_error = str(e)
            print(f"DEBUG: {self.name} CONNECT FAILED -> setting connected FALSE")
            print(f"DEBUG: {e}")

            logging.error(f"{self.name} connection failed : {e}")

            self.connected = False
            return False

    # --------------------------------------------------------

    def disconnect(self):

        self.stop_event.set()
        self.connected = False

        try:

            if self.serial and self.serial.is_open:
                self.serial.close()

        except Exception as e:
            self.last_error = str(e)
            print(f"DEBUG: {self.name} DISCONNECT -> setting connected FALSE")
            self.connected = False

        # Wait for the background read thread to actually exit,
        # so a rapid disconnect -> reconnect doesn't race with
        # the old thread still running.
        if self.read_thread and self.read_thread.is_alive():

            self.read_thread.join(timeout=SERIAL_TIMEOUT + 1)

        logging.info(f"{self.name} disconnected")

    # --------------------------------------------------------

    def send(self, message: str):

        print(f"DEBUG: send() called, connected={self.connected}, message={message}")

        if not self.connected:
            print("DEBUG: Not connected")
            return False

        try:

            logging.info(f"{self.name.upper()} TX: {message}")

            self.serial.write((message + "\n").encode())

            print("DEBUG: Write successful")

            return True

        except Exception as e:

            self.last_error = str(e)
            print(f"DEBUG: Write failed: {e}")

            logging.error(f"{self.name} send error : {e}")

            return False

    # --------------------------------------------------------

    def send_and_wait(self, message: str, timeout: float = COMMAND_TIMEOUT):
        """
        Sends a command and waits for an ACK/NACK line from the flight
        computer. Returns (sent: bool, ack: "ACK" | "NACK" | "TIMEOUT" | "NOT_SENT").
        """
        while not self.ack_queue.empty():
            try:
                self.ack_queue.get_nowait()
            except queue.Empty:
                break

        if not self.send(message):
            return False, "NOT_SENT"

        try:
            reply = self.ack_queue.get(timeout=timeout)
            return True, ("NACK" if reply.startswith("NACK") else "ACK")
        except queue.Empty:
            return True, "TIMEOUT"

    # --------------------------------------------------------

    def is_connected(self):

        return self.connected

    # --------------------------------------------------------

    def _read_line(self):
        """
        Returns the next complete line, terminated by \\n, \\r or \\r\\n.
        (pyserial's readline() only understands \\n, so firmware that ends
        packets with a bare \\r would glue packets together.)
        Returns b"" when no complete line arrived within the serial timeout.
        """
        while True:
            cut = -1
            for i, b in enumerate(self._buf):
                if b in (10, 13):
                    cut = i
                    break

            if cut != -1:
                line, self._buf = self._buf[:cut], self._buf[cut + 1:]
                if line.strip():
                    return line
                continue

            if len(self._buf) > 4096:       # runaway garbage, no terminator
                line, self._buf = self._buf, b""
                return line

            chunk = self.serial.read(max(1, self.serial.in_waiting))
            if not chunk:
                return b""
            self._buf += chunk

    def _emit_line(self, line: str, ok: bool, error: str = "", kind: str = "packet"):
        cb = self.line_callback
        if cb is None:
            return
        try:
            cb(self.name.lower(), line, ok, error, kind)
        except Exception:
            pass

    # --------------------------------------------------------

    def read_loop(self):
        """
        Continuously receives telemetry packets from the serial port.

        Runs forever in a background thread until stop_event is set.
        On a serial error, attempts a (blocking, non-recursive)
        reconnect and then keeps looping on this same stack frame
        instead of re-entering read_loop.
        """

        while not self.stop_event.is_set():

            packet = ""

            try:

                if self.serial is None or not self.serial.is_open:
                    raise serial.SerialException("Serial port closed")

                raw = self._read_line()

                if not raw:
                    continue

                packet = raw.decode(
                    "utf-8",
                    errors="ignore"
                ).strip()

                if not packet:
                    continue

                # ACK / NACK replies to uplink commands are not telemetry
                if packet.upper().startswith(("ACK", "NACK")):
                    self.ack_queue.put(packet.upper())
                    self._emit_line(packet, True)
                    continue

                # Firmware debug text (e.g. "GPS Coordinates:26.45, 80.19") is shown in the
                # serial log but is not telemetry, so it must not count as a corrupt packet.
                if not packet_parser.looks_like_telemetry(packet_parser.clean(packet)):
                    self._emit_line(packet, True, "", "info")
                    continue

                self.rx_packets += 1
                self.last_rx_time = time.time()

                logging.info(f"{self.name.upper()} RX: {packet}")

                telemetry = packet_parser.parse(packet)
                self.valid_packets += 1
                self._emit_line(packet, True)

                if self.name.lower() == "rocket":
                    telemetry_store.update_rocket(telemetry)
                    csv_logger.log_packet("ROCKET", telemetry)

                elif self.name.lower() == "cansat":
                    telemetry_store.update_cansat(telemetry)
                    csv_logger.log_packet("CANSAT", telemetry)

            except ValueError as e:

                self.invalid_packets += 1
                self.last_error = str(e)
                if packet:
                    self._emit_line(packet, False, str(e))
                logging.warning(
                    f"{self.name.upper()} INVALID PACKET: {e}"
                )

            except serial.SerialException as e:

                self.last_error = str(e)
                print(f"DEBUG: {self.name} SERIAL EXCEPTION -> setting connected FALSE")
                print(f"DEBUG: {e}")

                logging.error(
                    f"{self.name} Serial Error : {e}"
                )

                self.connected = False

                if AUTO_RECONNECT and not self.stop_event.is_set():

                    time.sleep(RECONNECT_INTERVAL)
                    self._attempt_reconnect()

                    if not self.connected:
                        break

                    continue

                else:

                    break

            except Exception as e:

                self.last_error = str(e)
                logging.exception(
                    f"{self.name} Unexpected Error : {e}"
                )

    # --------------------------------------------------------

    def _attempt_reconnect(self):
        """
        Blocking, iterative reconnect loop. Does NOT call read_loop
        itself -- it only re-establishes self.serial and returns.
        """

        logging.info(
            f"{self.name} attempting reconnection..."
        )

        while AUTO_RECONNECT and not self.stop_event.is_set():

            try:

                self.serial = serial.Serial(
                    self.port,
                    self.baudrate,
                    timeout=SERIAL_TIMEOUT,
                )

                self.connected = True
                self._buf = b""

                logging.info(
                    f"{self.name} reconnected."
                )

                return

            except Exception:

                logging.warning(
                    f"{self.name} reconnect failed..."
                )

                time.sleep(RECONNECT_INTERVAL)

    # --------------------------------------------------------

    def reconnect(self):

        self._attempt_reconnect()


# --------------------------------------------------------
# Serial Manager
# --------------------------------------------------------

class SerialManager:
    """
    Manages all serial connections used by the GCS.
    """

    def __init__(self):

        self.rocket = SerialConnection("Rocket")
        self.cansat = SerialConnection("CanSat")

    # ----------------------------------------------------

    def connect_rocket(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
    ):

        return self.rocket.connect(port, baudrate)

    # ----------------------------------------------------

    def connect_cansat(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
    ):

        return self.cansat.connect(port, baudrate)

    # ----------------------------------------------------

    def disconnect_rocket(self):

        self.rocket.disconnect()

    # ----------------------------------------------------

    def disconnect_cansat(self):

        self.cansat.disconnect()

    # ----------------------------------------------------

    def disconnect(self):

        self.disconnect_all()

    # ----------------------------------------------------

    def disconnect_all(self):

        self.disconnect_rocket()
        self.disconnect_cansat()

    # ----------------------------------------------------

    @staticmethod
    def frame_command(command: str) -> str:
        """'$CMD,<command>*HH\\r' with HH = hex XOR checksum of 'CMD,<command>'."""
        body = f"CMD,{command}"
        cs = 0
        for ch in body:
            cs ^= ord(ch)
        return f"${body}*{cs:02X}\r"

    def send_command(self, device: str, command: str, framed: bool = False, wait_ack: bool = False):
        """
        Uplink with optional checksum framing and ACK wait. Every call is
        written to the command audit log. Returns (sent, ack).
        """
        conn = self.rocket if device == "rocket" else self.cansat
        payload = self.frame_command(command) if framed else command

        if wait_ack:
            sent, ack = conn.send_and_wait(payload)
        else:
            sent, ack = conn.send(payload), ""

        csv_logger.audit_command(device, payload, sent, ack)
        return sent, ack

    # ----------------------------------------------------

    def send_rocket(self, command: str):

        return self.rocket.send(command)

    # ----------------------------------------------------

    def send_cansat(self, command: str):

        return self.cansat.send(command)

    # ----------------------------------------------------

    def set_line_callback(self, callback):
        """Registers fn(device, line, ok, error, kind) called for every received line."""
        self.rocket.line_callback = callback
        self.cansat.line_callback = callback

    # ----------------------------------------------------

    def status(self):

        return {
            "rocket": self.rocket.is_connected(),
            "cansat": self.cansat.is_connected(),
        }

    # ----------------------------------------------------

    def get_diagnostics(self):

        return {
            "rocket": self.rocket.get_diagnostics(),
            "cansat": self.cansat.get_diagnostics(),
        }

    # ----------------------------------------------------

    @staticmethod
    def available_ports():
        """
        Returns all available COM ports.
        """

        ports = []

        for port in serial.tools.list_ports.comports():

            ports.append(
                {
                    "device": port.device,
                    "description": port.description,
                    "manufacturer": port.manufacturer,
                }
            )

        return ports


# --------------------------------------------------------
# Global Instance
# --------------------------------------------------------

serial_manager = SerialManager()


# --------------------------------------------------------
# Standalone Test
# --------------------------------------------------------

if __name__ == "__main__":

    print("Available COM Ports\n")

    for port in serial_manager.available_ports():

        print(
            f"{port['device']}  ->  {port['description']}"
        )

    print("\nSerial Manager Ready.")