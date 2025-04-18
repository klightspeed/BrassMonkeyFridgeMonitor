#!/usr/bin/env python3

'''fridge_mqtt.py

Monitors a given Alpicool compatible fridge and sends
status updates to an MQTT broker
'''

import asyncio
import argparse
import logging
import sys

from typing import Optional
import paho.mqtt.client as mqtt

from fridge import Fridge, FridgeData


def publish_offline(mqttc: mqtt.Client, addr: str):
    '''Publish online=false to the MQTT broker'''

    mqttc.publish(f"fridge/{addr}/online", False)


def publish_status(mqttc: mqtt.Client,
                   addr: str,
                   data: FridgeData,
                   previous_data: Optional[FridgeData]
                  ):
    '''Publish the current fridge status to the MQTT broker'''

    if previous_data is None:
        mqttc.publish(f"fridge/{addr}/online", True)

    if previous_data is None or data != previous_data:
        info = data.to_dict()

        mqttc.publish(f'fridge/{addr}/state', info)


async def run(addr: str,
              bind: bool,
              poll: bool,
              pollinterval: int,
              mqttc: mqtt.Client
             ):
    '''Run the write-notify loop'''
    # pylint: disable=R0801
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
    '''fridge_mqtt.py entry point when run as a script'''
    # pylint: disable=R0801
    parser = argparse.ArgumentParser(
        prog='fridge_mqtt.py',
        description='Fridge monitor for Alpicool / Brass Monkey fridges',
        add_help=False
    )

    parser.add_argument(
        'address',
        help='Bluetooth address of fridge'
    )
    parser.add_argument(
        '-?',
        '--help',
        action='help',
        help='Show this help message and exit'
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
    host_group = parser.add_mutually_exclusive_group(required=True)
    host_group.add_argument(
        '-h',
        '--mqtt-host',
        type = str,
        help='MQTT host name / ip address'
    )
    host_group.add_argument(
        '-s',
        '--mqtt-socket',
        type = str,
        help='MQTT unix socket path'
    )
    parser.add_argument(
        '-T',
        '--mqtt-transport',
        type = str,
        choices = ['tcp', 'websockets', 'unix'],
        default = 'tcp',
        help='MQTT transport'
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

    mqtt_transport = args.mqtt_transport
    mqtt_host = args.mqtt_host

    if args.mqtt_socket is not None:
        mqtt_transport = 'unix'
        mqtt_host = args.mqtt_socket
    
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport=mqtt_transport)

    mqttc.connect(mqtt_host, args.mqtt_port, 60)

    mqttc.loop_start()

    try:
        asyncio.run(run(args.address, args.bind, args.loop, args.pollinterval, mqttc))
    except KeyboardInterrupt:
        sys.stderr.write('Exiting\n')
    finally:
        publish_offline(mqttc, args.address)
        mqttc.loop_stop()


if __name__ == '__main__':
    main()
