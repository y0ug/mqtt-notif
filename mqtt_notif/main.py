import datetime
import logging
import os
import re
import time

import apprise
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

APPRISE_URL = os.getenv("APPRISE_URL")
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_HOSTNAME = os.getenv("MQTT_HOSTNAME")
MQTT_PORT = os.getenv("MQTT_PORT")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

TOPIC = "mazenet/home/garage/door_state"

apprise_obj = apprise.Apprise()
apprise_obj.add(APPRISE_URL)

logger = logging.getLogger(__name__)

heartbeat_lost_delay = 90

# Dictionary to track each sensor's state and heartbeat data
sensors = {}


def parse_influxdb_line_protocol(line):
    regex = r"^(?P<measurement>\w+),?(?P<tags>[^ ]*) (?P<fields>[^ ]+)(?: (?P<timestamp>\d+))?$"
    match = re.match(regex, line)
    if match:
        measurement = match.group("measurement")
        tags = (
            {
                k: v
                for k, v in (tag.split("=") for tag in match.group("tags").split(","))
            }
            if match.group("tags")
            else {}
        )
        fields = {
            k: float(v) if v.replace(".", "", 1).isdigit() else v
            for k, v in (field.split("=") for field in match.group("fields").split(","))
        }
        timestamp = int(match.group("timestamp")) if match.group("timestamp") else None
        return measurement, tags, fields, timestamp
    else:
        logger.warning(f"Failed to parse line: {line}")
        return None, None, None, None


def send_telegram_message(message):
    if not apprise_obj.notify(body=message):
        logger.error("Failed to send Telegram alert.")


# MQTT Callbacks
def on_message(client, userdata, msg):
    global sensors

    topic = msg.topic
    payload = msg.payload.decode()
    logger.info(f"Received message: {payload}")

    measurement, tags, fields, timestamp = parse_influxdb_line_protocol(payload)
    if measurement is None or "location" not in tags:
        return

    sensor_location = tags["location"]

    # Initialize sensor data if not present
    if sensor_location not in sensors:
        sensors[sensor_location] = {
            "last_heartbeat": time.time(),
            "last_state": fields.get("state", 0),
            "heartbeat_lost": False,
        }

    sensor_data = sensors[sensor_location]

    # Check if heartbeat was lost
    if sensor_data["heartbeat_lost"]:
        sensor_data["heartbeat_lost"] = False
        lost_interval = datetime.timedelta(
            seconds=time.time() - sensor_data["last_heartbeat"]
        )
        logger.info(f"Sensor {sensor_location} back online after {lost_interval}")
        send_telegram_message(
            f"Sensor {sensor_location} back online after {lost_interval}"
        )

    # Update last heartbeat and state if changed
    sensor_data["last_heartbeat"] = time.time()
    if fields.get("state") != sensor_data["last_state"]:
        sensor_data["last_state"] = int(fields["state"])
        logger.info(
            f"Sensor {sensor_location} state changed to {sensor_data['last_state']}!"
        )
        send_telegram_message(
            f"Sensor {sensor_location} state changed to {sensor_data['last_state']}!"
        )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s ; %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    logger.info(f"starting mqtt-telegram on {TOPIC}")
    # MQTT Connection Setup
    client = mqtt.Client()
    client.on_message = on_message
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set()
    client.connect(MQTT_HOSTNAME, int(MQTT_PORT))
    client.subscribe([(TOPIC, 0)])

    # Run Loop with Heartbeat Check
    while True:
        client.loop(0.1)  # Process MQTT messages
        current_time = time.time()

        # Check each sensor for heartbeat timeout
        for location, sensor_data in sensors.items():
            if (
                not sensor_data["heartbeat_lost"]
                and current_time - sensor_data["last_heartbeat"] > heartbeat_lost_delay
            ):
                logger.warning(
                    f"Lost sensor {location} last seen {heartbeat_lost_delay} seconds ago"
                )
                send_telegram_message(
                    f"Lost sensor {location} last seen {heartbeat_lost_delay} seconds ago"
                )
                sensor_data["heartbeat_lost"] = (
                    True  # Set flag to avoid repeated alerts
                )

        time.sleep(1)


if __name__ == "__main__":
    main()
