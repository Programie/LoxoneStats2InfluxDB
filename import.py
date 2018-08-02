#! /usr/bin/env python3
import argparse
import json
import logging
import re
import requests
import sys
import time
from xml.etree import ElementTree
from influxdb import InfluxDBClient

try:
    from terminaltables import SingleTable
except ImportError:
    SingleTable = None

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
    files = {}

    html = miniserver_request(miniserver_config, "stats/").text.split("\n")

    for line in html:
        match = re.search('<a href="(.*)">(.*)</a>', line)
        if not match:
            continue

        files[match.group(1)] = match.group(2)

    return files


def get_uuid_from_filename(filename):
    return re.search("([a-f0-9\-]+)", filename).group(1)


def import_stats(logger, filename, miniserver_config, influxdb_client, measurement, tags, value_map):
    try:
        request = miniserver_request(miniserver_config, "stats/{}".format(filename))
        tree = ElementTree.fromstring(request.content)

        points = []

        for stat in tree.iter("S"):
            time_string = stat.get("T")

            time_string = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.mktime(time.strptime(time_string, "%Y-%m-%d %H:%M:%S"))))

            fields = {}

            for attribute, field in value_map.items():
                value = float(stat.get(attribute))

                logger.debug("Adding value {} from attribute '{}' to field '{}' with time {}".format(value, attribute, field, time_string))

                fields[field] = value

            points.append({
                "measurement": measurement,
                "tags": tags,
                "time": time_string,
                "fields": fields
            })

        influxdb_client.write_points(points)

        logger.info("Data written")
    except KeyboardInterrupt:
        raise
    except Exception:
        logger.error("Exception occurred, retrying in 10 seconds...")

        time.sleep(10)

        import_stats(logger, filename, miniserver_config, influxdb_client, measurement, tags, value_map)


def main():
    argument_parser = argparse.ArgumentParser(description="Import statistics from Loxone Miniserver into InfluxDB")

    argument_parser.add_argument("--config", "-c", help="the configuration file to use (default: config.json)", default="config.json")
    argument_parser.add_argument("--list", "-l", action="store_true", help="only list stats files")
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

    logger.info("Getting list of stats files from Miniserver")

    files = get_files(miniserver_config)

    logger.info("{} stats files to import".format(len(files)))

    if arguments.list:
        unique_stats = {}

        for filename, title in files.items():
            unique_stats[get_uuid_from_filename(filename)] = title

        stats_list = []

        for uuid, title in unique_stats.items():
            stats_list.append([uuid, re.search("(.*) [0-9]+$", title).group(1)])

        if SingleTable:
            table_rows = [
                ["UUID", "Name"]
            ]

            # noinspection PyCallingNonCallable
            print(SingleTable(table_rows + stats_list).table)
        else:
            for line in stats_list:
                print("\t".join(line))

        return

    influxdb_client = InfluxDBClient(influxdb_config["host"], influxdb_config["port"], influxdb_config["username"], influxdb_config["password"], influxdb_config["database"])

    for filename, title in files.items():
        logger.info("Next file: {}".format(filename))

        uuid = get_uuid_from_filename(filename)

        if uuid not in stats_map:
            logger.warning("UUID {} not mapped, skipping".format(uuid))
            continue

        map_entry = stats_map[uuid]

        measurement = map_entry["measurement"]

        if "tags" in map_entry:
            tags = map_entry["tags"]
        else:
            tags = None

        if "values" in map_entry:
            value_map = map_entry["values"]
        else:
            value_map = {
                "V": "value"
            }

        logger.info("Writing values into measurement '{}' with tags {}".format(measurement, tags))

        import_stats(logger, filename, miniserver_config, influxdb_client, measurement, tags, value_map)


if __name__ == "__main__":
    main()
