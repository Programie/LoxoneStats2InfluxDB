#! /usr/bin/env python3
import argparse
import json
import logging
import re
import requests
import sys
import time
from datetime import datetime
from xml.etree import ElementTree
from influxdb import InfluxDBClient

try:
    import argcomplete
except ImportError:
    argcomplete = None


def validate_config(config):
    if "miniserver" not in config:
        print("Miniserver not configured", file=sys.stderr)
        return False

    if "influxdb" not in config:
        print("InfluxDB not configured", file=sys.stderr)
        return False

    if "stats_map" not in config:
        print("Stats map not configured", file=sys.stderr)
        return False

    return True


def miniserver_request(config, path):
    request = requests.get("http://{}/{}".format(config["host"], path), auth=(config["username"], config["password"]))
    request.raise_for_status()

    return request


def get_files(miniserver_config):
    files = []

    html = miniserver_request(miniserver_config, "stats/").text.split("\n")

    for line in html:
        match = re.search('<a href="(.*)">.*</a>', line)
        if not match:
            continue

        files.append(match.group(1))

    return files


def import_stats(logger, filename, miniserver_config, influxdb_client, measurement, tags):
    try:
        request = miniserver_request(miniserver_config, "stats/{}".format(filename))
        tree = ElementTree.fromstring(request.content)

        points = []

        for stat in tree.iter("S"):
            time_string = stat.get("T")
            value_string = stat.get("V")

            time_string = datetime.strptime(time_string, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%SZ")

            logger.debug("Adding value {} with time {}".format(value_string, time_string))

            points.append({
                "measurement": measurement,
                "tags": tags,
                "time": time_string,
                "fields": {
                    "value": float(value_string)
                }
            })

        influxdb_client.write_points(points)

        logger.info("Data written")
    except KeyboardInterrupt:
        raise
    except Exception:
        logger.error("Exception occurred, retrying in 10 seconds...")

        time.sleep(10)

        import_stats(logger, filename, miniserver_config, influxdb_client, measurement, tags)


def main():
    argument_parser = argparse.ArgumentParser(description="Import statistics from Loxone Miniserver into InfluxDB")

    argument_parser.add_argument("--config", "-c", help="the configuration file to use (default: config.json)", default="config.json")
    argument_parser.add_argument("--quiet", "-q", action="store_true", help="only output warnings and errors")
    argument_parser.add_argument("--verbose", "-v", action="store_true", help="be more verbose")

    if argcomplete:
        argcomplete.autocomplete(argument_parser)

    arguments = argument_parser.parse_args()

    logger = logging.getLogger(__name__)

    if arguments.quiet:
        logger.setLevel(logging.WARNING)
    elif arguments.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    console_logger = logging.StreamHandler()
    console_logger.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s:%(name)s: %(message)s"))
    logger.addHandler(console_logger)

    try:
        with open(arguments.config) as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        print("configuration file not found, see readme for details", file=sys.stderr)
        exit(1)
        return

    if not validate_config(config):
        exit(1)
        return

    miniserver_config = config["miniserver"]
    influxdb_config = config["influxdb"]
    stats_map = config["stats_map"]

    influxdb_client = InfluxDBClient(influxdb_config["host"], influxdb_config["port"], influxdb_config["username"], influxdb_config["password"], influxdb_config["database"])

    logger.info("Getting list of stats files from Miniserver")

    files = get_files(miniserver_config)

    logger.info("{} stats files to import".format(len(files)))

    for filename in files:
        room_id = re.search("([a-f0-9\-]+)", filename).group(1)

        logger.info("Next file: {}".format(filename))

        if room_id not in stats_map:
            logger.warning("Room ID {} not mapped, skipping".format(room_id))
            continue

        map_entry = stats_map[room_id]

        measurement = map_entry["measurement"]
        tags = map_entry["tags"]

        logger.info("Writing values into measurement '{}' with tags {}".format(measurement, tags))

        import_stats(logger, filename, miniserver_config, influxdb_client, measurement, tags)


if __name__ == "__main__":
    main()
