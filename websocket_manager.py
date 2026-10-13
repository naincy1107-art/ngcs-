"""
websocket_manager.py
--------------------
Socket.IO manager for the Ground Control Station.

Responsibilities
----------------
- Manage all websocket connections.
- Send live Rocket telemetry.
- Send live CanSat telemetry.
- Broadcast connection status.
"""

import logging
from config import SOCKET_ASYNC_MODE, SOCKET_CORS
import threading
import time

from flask_socketio import SocketIO

from telemetry_store import telemetry_store
from serial_manager import serial_manager
from config import WEBSOCKET_UPDATE_RATE

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)

# ==========================================================
# Socket.IO Instance
# ==========================================================

socketio = SocketIO(
    cors_allowed_origins=SOCKET_CORS,
    async_mode=SOCKET_ASYNC_MODE,
)

# ==========================================================
# WebSocket Manager
# ==========================================================

class WebSocketManager:

    def __init__(self):

        self.running = False
        self.thread = None
        self.lock = threading.Lock()

    # ------------------------------------------------------

    def start(self):

        with self.lock:

            if self.running:
                return

            self.running = True

            self.thread = threading.Thread(
                target=self.broadcast_loop,
                daemon=True
            )

            self.thread.start()

    # ------------------------------------------------------

    def stop(self):

        with self.lock:
            self.running = False

    # ------------------------------------------------------

    def is_running(self):

        with self.lock:
            return self.running

    # ------------------------------------------------------

    def broadcast_loop(self):
        """
        Continuously broadcasts telemetry to every connected client.

        The running flag is read under the same lock used by
        start()/stop(), so there's a single consistent source of
        truth for thread state instead of relying on the GIL to
        make an unlocked read "happen to work".

        Each iteration is wrapped in try/except so a single failed
        emit (e.g. socketio not yet attached to the Flask app, a
        transient network hiccup, etc.) is logged and the loop keeps
        running -- instead of silently killing the entire broadcast
        thread for the rest of the flight.
        """

        interval = 1 / WEBSOCKET_UPDATE_RATE

        while True:

            with self.lock:
                if not self.running:
                    break

            try:

                if socketio.server is not None:
                    # Check if any active dashboard client is connected
                    eio = getattr(socketio.server, "eio", None)
                    if eio and hasattr(eio, "sockets") and len(eio.sockets) > 0:
                        snapshot = telemetry_store.snapshot()
                        snapshot["diagnostics"] = serial_manager.get_diagnostics()
                        socketio.emit(
                            "telemetry_update",
                            snapshot,
                        )

            except Exception as e:

                logging.exception(f"Broadcast loop error : {e}")

            time.sleep(interval)

        logging.info("Broadcast loop stopped.")

    # ------------------------------------------------------

    def emit_rocket(self):

        try:

            if socketio.server is not None:
                socketio.emit(
                    "rocket_update",
                    telemetry_store.get_rocket()
                )

        except Exception as e:

            logging.exception(f"emit_rocket failed : {e}")

    # ------------------------------------------------------

    def emit_cansat(self):

        try:

            if socketio.server is not None:
                socketio.emit(
                    "cansat_update",
                    telemetry_store.get_cansat()
                )

        except Exception as e:

            logging.exception(f"emit_cansat failed : {e}")

    # ------------------------------------------------------

    def emit_snapshot(self):

        try:

            if socketio.server is not None:
                socketio.emit(
                    "telemetry_update",
                    telemetry_store.snapshot()
                )

        except Exception as e:

            logging.exception(f"emit_snapshot failed : {e}")

    # ------------------------------------------------------

    def emit_status(self):

        try:

            if socketio.server is not None:
                socketio.emit(
                    "connection_status",
                    {
                        "rocket": telemetry_store.rocket_connected,
                        "cansat": telemetry_store.cansat_connected,
                    }
                )

        except Exception as e:

            logging.exception(f"emit_status failed : {e}")

    # ------------------------------------------------------

    def broadcast_message(
        self,
        event: str,
        data
    ):

        try:

            if socketio.server is not None:
                socketio.emit(event, data)

        except Exception as e:

            logging.exception(
                f"broadcast_message failed for event '{event}' : {e}"
            )


# ==========================================================
# Socket.IO Events
# ==========================================================

@socketio.on("connect")
def handle_connect():
    """
    Called whenever a dashboard connects.
    """

    logging.info("Dashboard Connected")

    socketio.emit(
        "connection_status",
        {
            "status": "connected"
        }
    )


# ----------------------------------------------------------

@socketio.on("disconnect")
def handle_disconnect():
    """
    Called whenever a dashboard disconnects.
    """

    logging.info("Dashboard Disconnected")


# ==========================================================
# Global Instance
# ==========================================================

websocket_manager = WebSocketManager()


def _emit_serial_line(device, line, ok, error, kind="packet"):
    """Forwards every raw received line (and why it was rejected) to the dashboard."""
    try:
        if socketio.server is not None:
            socketio.emit(
                "serial_line",
                {"device": device, "line": line, "ok": ok, "error": error, "kind": kind},
            )
    except Exception:
        pass


serial_manager.set_line_callback(_emit_serial_line)


# ==========================================================
# Standalone Test
# ==========================================================

if __name__ == "__main__":

    websocket_manager.start()

    print("WebSocket Manager Ready")