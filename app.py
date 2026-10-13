"""
app.py
------
Main entry point for the IN-SPACe Ground Control Station Backend.

Responsibilities
----------------
- Create the Flask application.
- Register REST API routes.
- Initialize Socket.IO.
- Enable CORS.
- Reset TelemetryStore state on startup.
- Start the WebSocket broadcasting thread.
- Launch the backend server.
"""

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from api import api
from websocket_manager import socketio, websocket_manager
from telemetry_store import telemetry_store

from config import (
    HOST,
    PORT,
    DEBUG,
    SECRET_KEY,
    PROJECT_NAME,
    VERSION,
    TERRAIN_MAX_UPLOAD_BYTES,
)

# ==========================================================
# Flask Application
# ==========================================================

app = Flask(__name__)

app.config["SECRET_KEY"] = SECRET_KEY

# Caps request body size (used by /api/predict/terrain image uploads).
app.config["MAX_CONTENT_LENGTH"] = TERRAIN_MAX_UPLOAD_BYTES

# Enable CORS
CORS(app)

# ==========================================================
# Register REST API
# ==========================================================

app.register_blueprint(
    api,
    url_prefix="/api"
)

# ==========================================================
# Initialize Socket.IO
# IMPORTANT:
# Must happen BEFORE websocket_manager.start()
# ==========================================================

socketio.init_app(app)

# ==========================================================
# Home & Static Routes
# ==========================================================

@app.route("/")
def home():

    return {
        "project": PROJECT_NAME,
        "version": VERSION,
        "status": "Running"
    }

@app.route("/dashboard")
@app.route("/newww.html")
def serve_dashboard():
    return send_from_directory(".", "newww.html")

@app.route("/script.js")
def serve_script():
    return send_from_directory(".", "script.js")

@app.route("/assets/<path:path>")
def serve_assets(path):
    return send_from_directory("assets", path)

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ==========================================================
# Startup
# ==========================================================

def start_backend():

    # Explicitly reset telemetry store on backend startup to wipe ghost data
    telemetry_store.reset()

    print("=" * 60)
    print(PROJECT_NAME)
    print("=" * 60)
    print(f"Version : {VERSION}")
    print(f"Running : http://{HOST}:{PORT}")
    print("REST API : http://localhost:5000/api")
    print("WebSocket : Enabled")
    print("=" * 60)

    # Start websocket background thread
    websocket_manager.start()

    # Start Flask + Socket.IO server
    socketio.run(
        app,
        host=HOST,
        port=PORT,
        debug=DEBUG,
        use_reloader=False,
        allow_unsafe_werkzeug=True
    )


# ==========================================================
# Global Error Handlers
# ==========================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify(
        {
            "success": False,
            "error": "Endpoint not found."
        }
    ), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify(
        {
            "success": False,
            "error": "Method not allowed."
        }
    ), 405


@app.errorhandler(500)
def internal_server_error(error):
    return jsonify(
        {
            "success": False,
            "error": "Internal server error."
        }
    ), 500


@app.errorhandler(413)
def payload_too_large(error):
    return jsonify(
        {
            "success": False,
            "error": "Uploaded file exceeds the maximum allowed size."
        }
    ), 413


if __name__ == "__main__":
    start_backend()