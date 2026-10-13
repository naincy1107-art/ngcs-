"""
api.py
------
REST API for the Ground Control Station.

This module provides endpoints used by the frontend to:

- Connect Rocket
- Connect CanSat
- Disconnect Rocket
- Disconnect CanSat
- Start/Stop CSV Logging
- Get Telemetry
- Get System Status
- List available COM ports
- Terrain Classification Inference
"""

from flask import Blueprint, jsonify, request

from config import DEFAULT_BAUDRATE
from telemetry_store import telemetry_store
from packet_parser import packet_parser
from replay_manager import replay_manager
from csv_logger import csv_logger
from serial_manager import serial_manager
from ml_engine import terrain_classifier, predict_terrain as ml_predict_terrain

# ==========================================================
# Blueprint
# ==========================================================

api = Blueprint(
    "api",
    __name__,
)

# ==========================================================
# Telemetry
# ==========================================================

@api.route("/telemetry", methods=["GET"])
def telemetry():

    try:
        return jsonify(
            telemetry_store.snapshot()
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# Status
# ==========================================================

@api.route("/status", methods=["GET"])
def status():

    try:
        snapshot = telemetry_store.snapshot()
        diag = serial_manager.get_diagnostics()

        return jsonify(
            {
                "rocket_connected": telemetry_store.rocket_connected,
                "cansat_connected": telemetry_store.cansat_connected,
                "logging": csv_logger.is_logging(),
                "uptime": snapshot["uptime"],
                "rocket": diag["rocket"],
                "cansat": diag["cansat"],
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# Available Ports
# ==========================================================

@api.route("/ports", methods=["GET"])
def ports():

    try:
        return jsonify(
            {
                "success": True,
                "ports": serial_manager.available_ports(),
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# Rocket Connection
# ==========================================================

@api.route("/connect/rocket", methods=["POST"])
def connect_rocket():

    try:
        data = request.get_json(silent=True) or {}

        port = data.get("port")
        if not port or port == "request":
            return jsonify(
                {
                    "success": False,
                    "rocket_connected": False,
                    "error": "No COM port selected. Please select a physical port."
                }
            ), 400

        raw_baud = data.get("baudrate", DEFAULT_BAUDRATE)
        try:
            baudrate = int(raw_baud)
        except (TypeError, ValueError):
            baudrate = DEFAULT_BAUDRATE

        success = serial_manager.connect_rocket(
            port=port,
            baudrate=baudrate,
        )

        if success:
            telemetry_store.set_rocket_connection(True)
            print("DEBUG: rocket_connected -> TRUE")

        return jsonify(
            {
                "success": success,
                "rocket_connected": success,
                "message": (
                    "Rocket connected successfully."
                    if success else
                    "Rocket connection failed."
                )
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# CanSat Connection
# ==========================================================

@api.route("/connect/cansat", methods=["POST"])
def connect_cansat():

    try:
        data = request.get_json(silent=True) or {}

        port = data.get("port")
        if not port or port == "request":
            return jsonify(
                {
                    "success": False,
                    "cansat_connected": False,
                    "error": "No COM port selected. Please select a physical port."
                }
            ), 400

        raw_baud = data.get("baudrate", DEFAULT_BAUDRATE)
        try:
            baudrate = int(raw_baud)
        except (TypeError, ValueError):
            baudrate = DEFAULT_BAUDRATE

        success = serial_manager.connect_cansat(
            port=port,
            baudrate=baudrate,
        )

        if success:
            telemetry_store.set_cansat_connection(True)

        return jsonify(
            {
                "success": success,
                "cansat_connected": success,
                "message": (
                    "CanSat connected successfully."
                    if success else
                    "CanSat connection failed."
                )
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# Rocket Disconnect
# ==========================================================

@api.route("/disconnect/rocket", methods=["POST"])
def disconnect_rocket():

    try:
        serial_manager.disconnect_rocket()
        print("DEBUG: rocket_connected -> FALSE")

        telemetry_store.set_rocket_connection(False)

        return jsonify(
            {
                "success": True,
                "message": "Rocket disconnected successfully."
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# CanSat Disconnect
# ==========================================================

@api.route("/disconnect/cansat", methods=["POST"])
def disconnect_cansat():

    try:
        serial_manager.disconnect_cansat()

        telemetry_store.set_cansat_connection(False)

        return jsonify(
            {
                "success": True,
                "message": "CanSat disconnected successfully."
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# CSV Logging
# ==========================================================

@api.route("/logging/start", methods=["POST"])
def start_logging():

    try:
        csv_logger.start_logging()

        return jsonify(
            {
                "success": True,
                "logging": csv_logger.is_logging(),
                "message": "CSV logging started."
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ----------------------------------------------------------

@api.route("/logging/stop", methods=["POST"])
def stop_logging():

    try:
        csv_logger.stop_logging()

        return jsonify(
            {
                "success": True,
                "logging": csv_logger.is_logging(),
                "message": "CSV logging stopped."
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# Health Check
# ==========================================================

@api.route("/health", methods=["GET"])
def health():

    try:
        snapshot = telemetry_store.snapshot()

        return jsonify(
            {
                "success": True,
                "status": "OK",
                "backend": "Running",
                "rocket_connected": snapshot["rocket_connected"],
                "cansat_connected": snapshot["cansat_connected"],
                "logging": csv_logger.is_logging(),
                "uptime": snapshot["uptime"],
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# Ping
# ==========================================================

@api.route("/ping", methods=["GET"])
def ping():

    return jsonify(
        {
            "success": True,
            "response": "pong"
        }
    )


# ==========================================================
# Rocket Command
# ==========================================================

@api.route("/command/rocket", methods=["POST"])
def command_rocket():

    try:
        if not serial_manager.rocket.is_connected():
            return jsonify(
                {
                    "success": False,
                    "error": "Rocket is not connected."
                }
            )

        data = request.get_json(silent=True) or {}
        command = data.get("command", "START_TELEMETRY")

        framed = bool(data.get("framed", False))
        wait_ack = bool(data.get("wait_ack", False))
        success, ack = serial_manager.send_command("rocket", command, framed, wait_ack)

        return jsonify(
            {
                "success": success,
                "command": command,
                "ack": ack,
                "message": (
                    f"Rocket command '{command}' sent successfully."
                    if success else
                    f"Rocket command '{command}' failed to send."
                )
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# CanSat Command
# ==========================================================

@api.route("/command/cansat", methods=["POST"])
def command_cansat():

    try:
        if not serial_manager.cansat.is_connected():
            return jsonify(
                {
                    "success": False,
                    "error": "CanSat is not connected."
                }
            )

        data = request.get_json(silent=True) or {}
        command = data.get("command", "START_TELEMETRY")

        framed = bool(data.get("framed", False))
        wait_ack = bool(data.get("wait_ack", False))
        success, ack = serial_manager.send_command("cansat", command, framed, wait_ack)

        return jsonify(
            {
                "success": success,
                "command": command,
                "ack": ack,
                "message": (
                    f"CanSat command '{command}' sent successfully."
                    if success else
                    f"CanSat command '{command}' failed to send."
                )
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# ML - Terrain Classification Endpoint
# ==========================================================

@api.route("/predict/terrain", methods=["POST"])
def predict_terrain():
    """
    Accepts an uploaded image frame (multipart/form-data with key 'image' or 'file',
    or raw body bytes), runs it through MobileNetV3 terrain classifier,
    stores the prediction in TelemetryStore, and returns JSON.
    """
    try:
        image_bytes = None
        # Read the raw body first: touching request.files/form consumes the stream,
        # which made raw-body uploads (curl --data-binary, octet-stream) arrive empty.
        raw_body = request.get_data(cache=True)
        if "image" in request.files:
            file = request.files["image"]
            if file and file.filename != "":
                image_bytes = file.read()
        elif "file" in request.files:
            file = request.files["file"]
            if file and file.filename != "":
                image_bytes = file.read()
        else:
            image_bytes = raw_body

        if not image_bytes:
            return jsonify(
                {
                    "success": False,
                    "error": "No image data uploaded."
                }
            ), 400

        future = terrain_classifier.predict_async(image_bytes)
        prediction = future.result()

        telemetry_store.update_terrain(prediction)

        return jsonify(
            {
                "success": True,
                "class_name": prediction["class_name"],
                "confidence": prediction["confidence"],
                "probabilities": prediction["probabilities"],
                "available": True,
                # True when no .tflite model is loaded and the placeholder fallback answered
                "simulated": not terrain_classifier.is_ready(),
            }
        )

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e)
            }
        ), 500


# ==========================================================
# HTTP Telemetry Injection (simulated hardware / HIL testing)
# ==========================================================

@api.route("/telemetry/inject", methods=["POST"])
def inject_telemetry():
    """
    JSON : {"device": "rocket"|"cansat", "packet": "<csv>"}   (or "raw": "<csv>")
           {"device": "...", "packets": ["<csv>", [field, ...], ...]}
    Raw  : text/plain CSV body, device via ?device=rocket|cansat
    Packets go through the same parser and store as serial data.
    """
    return _inject(None)


@api.route("/telemetry/<device>", methods=["POST"])
def inject_telemetry_for_device(device):
    """Same as /telemetry/inject with the device in the URL (rocket | cansat)."""
    return _inject(device)


def _inject(path_device):
    try:
        data = request.get_json(silent=True)
        if data is None:
            data = {"packet": request.get_data(as_text=True)}

        device = str(
            path_device or data.get("device") or request.args.get("device", "rocket")
        ).lower()
        if device not in ("rocket", "cansat"):
            return jsonify({"success": False, "error": "device must be 'rocket' or 'cansat'."}), 400

        packets = data.get("packets")
        if packets is None:
            single = data.get("packet") or data.get("raw")
            packets = [single] if single else []

        if not packets:
            return jsonify({"success": False, "error": "No packet data supplied."}), 400

        accepted, errors = 0, []

        for i, p in enumerate(packets):
            try:
                if isinstance(p, list):
                    p = ",".join(str(x) for x in p)
                parsed = packet_parser.parse(str(p))
            except ValueError as e:
                errors.append({"index": i, "error": str(e)})
                continue

            if device == "rocket":
                telemetry_store.update_rocket(parsed)
                csv_logger.log_packet("ROCKET", parsed)
            else:
                telemetry_store.update_cansat(parsed)
                csv_logger.log_packet("CANSAT", parsed)
            accepted += 1

        return jsonify(
            {
                "success": accepted > 0,
                "accepted": accepted,
                "rejected": len(errors),
                "errors": errors,
            }
        ), (200 if accepted else 400)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================================
# Flight Replay
# ==========================================================

@api.route("/replay/start", methods=["POST"])
def replay_start():
    """
    multipart/form-data: file=<session csv>, optional speed, loop
    """
    try:
        upload = request.files.get("file")
        if upload is None or upload.filename == "":
            return jsonify({"success": False, "error": "Upload a session CSV as 'file'."}), 400

        form = request.form
        try:
            speed = float(form.get("speed", 1.0))
        except ValueError:
            speed = 1.0
        loop = str(form.get("loop", "false")).lower() in ("1", "true", "yes")

        text = upload.read().decode("utf-8", errors="ignore")
        info = replay_manager.start(text, speed=speed, loop=loop)

        return jsonify({"success": True, **info})

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api.route("/replay/stop", methods=["POST"])
def replay_stop():
    replay_manager.stop()
    return jsonify({"success": True, "message": "Replay stopped."})


@api.route("/replay/status", methods=["GET"])
def replay_status():
    return jsonify({"success": True, **replay_manager.status()})
