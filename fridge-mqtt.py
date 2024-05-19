#!/usr/bin/env python3

import asyncio
import argparse
import logging
import sys

import paho.mqtt.client as mqtt
from fridge import Fridge, FridgeData
from typing import Optional


def publish_offline(mqttc: mqtt.Client, addr: str):
    mqttc.publish(f"fridge/{addr}/online", False)


def publish_status(mqttc: mqtt.Client, addr: str, data: FridgeData, previous_data: Optional[FridgeData]):
    if previous_data is None:
        mqttc.publish(f"fridge/{addr}/online", True)

    if previous_data is None or data != previous_data:
        info = data.to_dict()

        mqttc.publish(f'fridge/{addr}/state', info)


async def run(addr: str, bind: bool, poll: bool, pollinterval: int, mqttc: mqtt.Client):
    async with Fridge(addr) as fridge:
        if bind:
            await asyncio.wait_for(fridge.bind(), 30)

        try:
            query_response = await asyncio.wait_for(fridge.query(), 5)
        except TimeoutError:
            pass
        else:
            publish_status(mqttc, addr, query_response, None)
            last_status = query_response

        while poll:
            await asyncio.sleep(pollinterval)

            try:
                await asyncio.wait_for(fridge.query(), 5)
            except TimeoutError:
                if last_status is not None:
                    publish_offline(mqttc, addr)
                last_status = None
            else:
                publish_status(mqttc, addr, query_response, last_status)
                last_status = query_response


def main():
    parser = argparse.ArgumentParser(
        prog='fridge-mqtt.py',
        description='Fridge monitor for Alpicool / Brass Monkey fridges'
    )

    parser.add_argument(
        'address',
        help='Bluetooth address of fridge'
    )
    parser.add_argument(
        '-b',
        '--bind',
        action='store_true',
        help='Press settings button on fridge to confirm fridge selection'
    )
    parser.add_argument(
        '-l',
        '--loop',
        action='store_true',
        help='Poll at regular intervals (default: query once)'
    )
    parser.add_argument(
        '-t',
        '--pollinterval',
        type = int,
        default=10,
        help='Poll interval in seconds (default: 10)'
    )
    parser.add_argument(
        '-h',
        '--mqtt-host',
        type = str,
        help='MQTT host name / ip address'
    )
    parser.add_argument(
        '-p',
        '--mqtt-port',
        type = int,
        default = 1883,
        help='MQTT port'
    )

    args = parser.parse_args()

    logging.basicConfig()

    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    mqttc.connect(args.mqtt_host, args.mqtt_port, 60)

    mqttc.loop_start()

    try:
        asyncio.run(run(args.address, args.bind, args.loop, args.pollinterval, mqttc))
    except KeyboardInterrupt:
        sys.stderr.write('Exiting\n')
        return
    finally:
        mqttc.publish(f'fridge/{args.address}/online', False)
        mqttc.loop_stop()


if __name__ == '__main__':
    main()
