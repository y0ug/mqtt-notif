import asyncio
import datetime
import logging
import os
import re
import ssl
import time
import importlib.metadata

import aiomqtt
import apprise
from dotenv import load_dotenv

version = importlib.metadata.version("mqtt-notif")


load_dotenv()

APPRISE_URL = os.getenv("APPRISE_URL")
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_HOSTNAME = os.getenv("MQTT_HOSTNAME")
MQTT_PORT = os.getenv("MQTT_PORT")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

TOPIC = "mazenet/home/garage/door_state"

apobj = apprise.Apprise()
apobj.add(APPRISE_URL)

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


async def send_telegram_message(message):
    results = await apobj.async_notify(body=message)
    if not results:
        logger.error("failed to send notification.")


async def process_sensor(msg: aiomqtt.Message):
    global sensors

    # topic = msg.topic
    payload = msg.payload.decode()
    logger.info(f"received {payload}")

    measurement, tags, fields, timestamp = parse_influxdb_line_protocol(payload)
    if measurement is None or "location" not in tags:
        return

    sensor_location = tags["location"]

    # Initialize sensor data if not present
    if sensor_location not in sensors:
        sensors[sensor_location] = {
            "last_heartbeat": time.time(),
            "last_state": int(fields.get("state", 0)),
            "heartbeat_lost": False,
        }
        sensor_data = sensors[sensor_location]
        logger.info(
            f"new sensor {sensor_location} with state {sensor_data['last_state']}"
        )
        await send_telegram_message(
            f"New sensor {sensor_location} with state {sensor_data['last_state']}"
        )

    sensor_data = sensors[sensor_location]

    # Check if heartbeat was lost
    if sensor_data["heartbeat_lost"]:
        sensor_data["heartbeat_lost"] = False
        lost_interval = datetime.timedelta(
            seconds=time.time() - sensor_data["last_heartbeat"]
        )
        logger.info(f"Sensor {sensor_location} back online after {lost_interval}")
        await send_telegram_message(
            f"Sensor {sensor_location} back online after {lost_interval}"
        )

    # Update last heartbeat and state if changed
    sensor_data["last_heartbeat"] = time.time()
    if fields.get("state") != sensor_data["last_state"]:
        sensor_data["last_state"] = int(fields["state"])
        logger.info(
            f"Sensor {sensor_location} state changed to {sensor_data['last_state']}!"
        )
        await send_telegram_message(
            f"Sensor {sensor_location} state changed to {sensor_data['last_state']}!"
        )


async def check_heartbeat():
    while True:
        current_time = time.time()

        # Check each sensor for heartbeat timeout
        for location, sensor_data in sensors.items():
            if (
                not sensor_data["heartbeat_lost"]
                and current_time - sensor_data["last_heartbeat"] > heartbeat_lost_delay
            ):
                logger.warning(
                    f"lost sensor {location} last seen {heartbeat_lost_delay} seconds ago"
                )
                await send_telegram_message(
                    f"Lost sensor {location} last seen {heartbeat_lost_delay} seconds ago"
                )
                sensor_data["heartbeat_lost"] = (
                    True  # Set flag to avoid repeated alerts
                )

        await asyncio.sleep(1)


async def main_async():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s; %(name)s; %(levelname)s; %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    logger.setLevel(logging.INFO)
    logger.info(f"starting mqtt-telegram v{version} on {TOPIC}")

    asyncio.create_task(check_heartbeat())

    interval = 5
    client = aiomqtt.Client(
        hostname=MQTT_HOSTNAME,
        port=int(MQTT_PORT),
        username=MQTT_USERNAME,
        password=MQTT_PASSWORD,
        tls_context=ssl.create_default_context(),
    )
    while True:
        try:
            async with client:
                await client.subscribe(TOPIC)
                async for message in client.messages:
                    if message.topic.matches(TOPIC):
                        await process_sensor(message)
        except aiomqtt.MqttError:
            logger.exception(f"connection lost; reconnecting in {interval} seconds ...")
            await asyncio.sleep(interval)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
