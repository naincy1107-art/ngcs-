from packet_parser import packet_parser
from telemetry_store import telemetry_store
from csv_logger import csv_logger, CSV_PATH
from config import TEAM_ID

# Sample telemetry packet
packet = (
    f"{TEAM_ID},"
    "123,"
    "1,"
    "120.5,"
    "101325,"
    "30.2,"
    "11.80,"
    "38458,"
    "26.4511,"
    "80.1907,"
    "250.0,"
    "8,"
    "0.1|0.2|0.3,"
    "0.01|0.02|0.03,"
    "IDLE,"
    "TEST"
)

telemetry = packet_parser.parse(packet)

telemetry_store.update_rocket(telemetry)

csv_logger.start_logging()

csv_logger.log_packet("ROCKET", telemetry)

csv_logger.stop_logging()

print(f"Telemetry written to {CSV_PATH}")