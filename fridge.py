#!/usr/bin/env python3

import struct
import asyncio
import argparse
import logging
import sys
import json

from asyncio import Future
from enum import Enum

from typing import Optional, Union, Callable, Any

from dataclasses import dataclass

from bleak import BleakClient
from bleak.exc import BleakError
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic


service_uid = '1234'
service_uuid = '00001234-0000-1000-8000-00805f9b34fb'
command_uid = '1235'
command_uuid = '00001235-0000-1000-8000-00805f9b34fb'
notify_uid = '1236'
notify_uuid = '00001236-0000-1000-8000-00805f9b34fb'

logger = logging.getLogger(__name__)


class FridgeCommand(int, Enum):
    Bind = 0
    Query = 1
    Set = 2
    Reset = 4
    SetUnit1Target = 5
    SetUnit2Target = 6


class FridgeRunMode(int, Enum):
    Max = 0
    Eco = 1


class FridgeBatterySaver(int, Enum):
    Low = 0
    Mid = 1
    High = 2


class FridgeTemperatureUnit(int, Enum):
    Celsius = 0
    Farenheit = 1


@dataclass
class FridgeUnitData(object):
    target_temperature: int
    hysteresis: int
    temperature_correction_hot: int
    temperature_correction_mid: int
    temperature_correction_cold: int
    temperature_correction_halt: int
    current_temperature: int


@dataclass
class FridgeData(object):
    controls_locked: bool
    powered_on: bool
    run_mode: FridgeRunMode
    battery_saver: FridgeBatterySaver
    max_selectable_temperature: int
    min_selectable_temperature: int
    start_delay: int
    temperature_unit: FridgeTemperatureUnit
    battery_charge_percent: int
    battery_voltage: float
    running_status: Optional[int]
    unit1: FridgeUnitData
    unit2: Optional[FridgeUnitData]


def decode_unit1_data(data: Union[bytes, bytearray]) -> FridgeUnitData:
    target_temperature, hysteresis, \
    temperature_correction_hot, temperature_correction_mid, \
    temperature_correction_cold, temperature_correction_halt, \
    current_temperature = \
        struct.unpack_from('>bxxbxxbbbbb', data, 4)

    return FridgeUnitData(
        target_temperature = target_temperature,
        hysteresis = hysteresis,
        temperature_correction_hot = temperature_correction_hot,
        temperature_correction_mid = temperature_correction_mid,
        temperature_correction_cold = temperature_correction_cold,
        temperature_correction_halt = temperature_correction_halt,
        current_temperature = current_temperature
    )


def decode_unit2_data(data: Union[bytes, bytearray]) -> Optional[FridgeUnitData]:
    if len(data) < 28:
        return None

    target_temperature, hysteresis, \
    temperature_correction_hot, temperature_correction_mid, \
    temperature_correction_cold, temperature_correction_halt, \
    current_temperature = \
        struct.unpack_from('>bxxbbbbbb', data, 18)

    return FridgeUnitData(
        target_temperature = target_temperature,
        hysteresis = hysteresis,
        temperature_correction_hot = temperature_correction_hot,
        temperature_correction_mid = temperature_correction_mid,
        temperature_correction_cold = temperature_correction_cold,
        temperature_correction_halt = temperature_correction_halt,
        current_temperature = current_temperature
    )


def decode_fridge_data(data: Union[bytes, bytearray]) -> FridgeData:
    if len(data) < 18:
        raise Exception('Packet too short')

    controls_locked, powered_on, run_mode, battery_saver, \
    max_selectable_temperature, min_selectable_temperature, \
    start_delay, temperature_unit, \
    battery_charge_percent, battery_voltage_int, battery_voltage_frac = \
        struct.unpack_from('>??BBxbbxBBxxxxxBBB', data, 0)

    running_status = None

    if len(data) >= 28:
        running_status = struct.unpack_from('B', data, 28)

    battery_voltage = battery_voltage_int + battery_voltage_frac / 10

    return FridgeData(
        controls_locked = controls_locked,
        powered_on = powered_on,
        run_mode = FridgeRunMode(run_mode),
        battery_saver = FridgeBatterySaver(battery_saver),
        max_selectable_temperature = max_selectable_temperature,
        min_selectable_temperature = min_selectable_temperature,
        start_delay = start_delay,
        temperature_unit = FridgeTemperatureUnit(temperature_unit),
        battery_charge_percent = battery_charge_percent,
        battery_voltage = battery_voltage,
        running_status = running_status,
        unit1 = decode_unit1_data(data),
        unit2 = decode_unit2_data(data)
    )


def create_packet(data: bytes) -> bytes:
    pkt = b'\xFE\xFE' + struct.pack('B', len(data) + 2) + data
    pkt += struct.pack('>H', sum(int(v) for v in pkt))
    return pkt


def get_packet_data(data: bytes) -> bytes:
    if len(data) <= 2:
        raise Exception('Packet is too small')

    if data[:2] != b'\xFE\xFE':
        raise Exception('Invalid frame header')

    pktlen = struct.unpack_from('B', data, 2)[0]

    if pktlen != len(data) - 3:
        raise Exception('Content length does not match')

    csum = struct.unpack_from('>H', data[-2:])[0]

    if csum != sum(int(v) for v in data[:-2]):
        raise Exception('Invalid checksum')

    return data[3:-2]


def encode_bind_command() -> bytes:
    return create_packet(struct.pack('B', FridgeCommand.Bind))


def encode_query_command() -> bytes:
    return create_packet(struct.pack('B', FridgeCommand.Query))


def encode_set_command(data: FridgeData) -> bytes:
    if data.unit2 is None:
        return create_packet(struct.pack(
            '>??BBbbbbBBbbbb',
            FridgeCommand.Set,
            data.controls_locked, data.powered_on, data.run_mode, data.battery_saver,
            data.unit1.target_temperature, data.max_selectable_temperature,
            data.min_selectable_temperature, data.unit1.hysteresis,
            data.start_delay, data.temperature_unit,
            data.unit1.temperature_correction_hot, data.unit1.temperature_correction_mid,
            data.unit1.temperature_correction_cold, data.unit1.temperature_correction_halt
        ))
    else:
        return create_packet(struct.pack(
            '>??BBbbbbBBbbbbbxxbbbbbxxx',
            FridgeCommand.Set,
            data.controls_locked, data.powered_on, data.run_mode, data.battery_saver,
            data.unit1.target_temperature, data.max_selectable_temperature,
            data.min_selectable_temperature, data.unit1.hysteresis,
            data.start_delay, data.temperature_unit,
            data.unit1.temperature_correction_hot, data.unit1.temperature_correction_mid,
            data.unit1.temperature_correction_cold, data.unit1.temperature_correction_halt,
            data.unit2.target_temperature, data.unit2.hysteresis,
            data.unit2.temperature_correction_hot, data.unit2.temperature_correction_mid,
            data.unit2.temperature_correction_cold, data.unit2.temperature_correction_halt
        ))


def encode_reset_command() -> bytes:
    return create_packet(struct.pack('B', FridgeCommand.Reset))


def encode_set_unit1_target_command(temp: int) -> bytes:
    return create_packet(struct.pack('Bb', FridgeCommand.SetUnit1Target, temp))


def encode_set_unit2_target_command(temp: int) -> bytes:
    return create_packet(struct.pack('Bb', FridgeCommand.SetUnit2Target, temp))


class Fridge(object):
    on_query_response: Callable[[FridgeData], Any] = None

    command_characteristic: Optional[BleakGATTCharacteristic] = None
    notify_characteristic: Optional[BleakGATTCharacteristic] = None
    client: BleakClient = None

    _bind_result_future: Optional[Future[int]] = None
    _query_result_future: Optional[Future[FridgeData]] = None
    _set_result_future: Optional[Future[FridgeData]] = None
    _reset_result_future: Optional[Future[FridgeData]] = None
    _set_unit1_result_future: Optional[Future[FridgeData]] = None
    _set_unit2_result_future: Optional[Future[FridgeData]] = None

    def __init__(self, client: Union[BleakClient, BLEDevice, str]):
        if isinstance(client, BleakClient):
            self.client = client
        else:
            self.client = BleakClient(client)

    async def connect(self):
        for retrynum in range(0, 2):
            try:
                await self.client.connect()
                break
            except BleakError as e:
                if e.args[0] == 'failed to discover services, device disconnected':
                    logger.info('Retrying after connect failed with: %s', e.args[0])
                    continue
                raise
            except TimeoutError:
                continue
        else:
            await self.client.connect()

        for h, c in self.client.services.characteristics.items():
            if c.service_uuid == service_uid or c.service_uuid == service_uuid:
                if c.uuid == command_uid or c.uuid == command_uuid:
                    self.command_characteristic = c
                elif c.uuid == notify_uid or c.uuid == notify_uuid:
                    self.notify_characteristic = c

        if self.command_characteristic is None or self.notify_characteristic is None:
            self.disconnect()
            raise Exception('Required GATT characteristics not found')

        await self.client.start_notify(self.notify_characteristic, self._notify_callback)

    async def disconnect(self):
        await self.client.disconnect()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.disconnect()

    def _notify_callback(self, sender: BleakGATTCharacteristic, pkt: bytearray):
        data = get_packet_data(pkt)

        if len(data) < 2:
            return

        cmd = struct.unpack_from('B', data, 0)[0]

        if cmd == FridgeCommand.Bind:
            self.notify_bind(struct.unpack_from('B', data, 1)[0])
        elif cmd == FridgeCommand.Query:
            self.notify_query(decode_fridge_data(data[1:]))
        elif cmd == FridgeCommand.Set:
            self.notify_set(decode_fridge_data(data[1:]))
        elif cmd == FridgeCommand.Reset:
            self.notify_reset(decode_fridge_data(data[1:]))
        elif cmd == FridgeCommand.SetUnit1Target:
            self.notify_set_unit1_target_temperature(struct.unpack_from('b', data, 1)[0])
        elif cmd == FridgeCommand.SetUnit2Target:
            self.notify_set_unit2_target_temperature(struct.unpack_from('b', data, 1)[0])

    def notify_bind(self, data: int):
        if isinstance(self._bind_result_future, Future):
            self._bind_result_future.set_result(data)

    def notify_query(self, data: FridgeData):
        if self.on_query_response is not None:
            self.on_query_response(data)

        if isinstance(self._query_result_future, Future):
            self._query_result_future.set_result(data)

    def notify_set(self, data: FridgeData):
        if isinstance(self._set_result_future, Future):
            self._set_result_future.set_result(data)

    def notify_reset(self, data: FridgeData):
        if isinstance(self._reset_result_future, Future):
            self._reset_result_future.set_result(data)

    def notify_set_unit1_target_temperature(self, data: int):
        if isinstance(self._set_unit1_result_future, Future):
            self._set_unit1_result_future.set_result(data)

    def notify_set_unit2_target_temperature(self, data: int):
        if isinstance(self._set_unit2_result_future, Future):
            self._set_unit2_result_future.set_result(data)

    async def send_command(self, pkt: bytes):
        await self.client.write_gatt_char(self.command_characteristic, pkt, response = True)

    async def send_bind_command(self):
        pkt = encode_bind_command()
        await self.send_command(pkt)

    async def send_query_command(self):
        pkt = encode_query_command()
        await self.send_command(pkt)

    async def send_set_command(self, data: FridgeData):
        pkt = encode_set_command(data)
        await self.send_command(pkt)

    async def send_reset_command(self):
        pkt = encode_reset_command()
        await self.send_command(pkt)

    async def send_set_unit1_target_temperature_command(self, target_temperature: int):
        pkt = encode_set_unit1_target_command(target_temperature)
        await self.send_command(pkt)

    async def send_set_unit2_target_temperature_command(self, target_temperature: int):
        pkt = encode_set_unit2_target_command(target_temperature)
        await self.send_command(pkt)

    async def bind(self) -> int:
        loop = asyncio.get_event_loop()
        self._bind_result_future = loop.create_future()
        await self.send_bind_command()
        return await self._bind_result_future

    async def query(self) -> FridgeData:
        loop = asyncio.get_event_loop()
        self._query_result_future = loop.create_future()
        await self.send_query_command()
        return await self._query_result_future

    async def set(self, data: FridgeData) -> FridgeData:
        loop = asyncio.get_event_loop()
        self._set_result_future = loop.create_future()
        await self.send_set_command(data)
        return await self._set_result_future

    async def reset(self) -> FridgeData:
        loop = asyncio.get_event_loop()
        self._reset_result_future = loop.create_future()
        await self.send_reset_command()
        return await self._reset_result_future

    async def set_unit1_target_temperature(self, target_temperature: int) -> int:
        loop = asyncio.get_event_loop()
        self._set_unit1_result_future = loop.create_future()
        await self.send_set_unit1_target_temperature_command(target_temperature)
        return await self._set_unit1_result_future

    async def set_unit2_target_temperature(self, target_temperature: int) -> int:
        loop = asyncio.get_event_loop()
        self._set_unit2_result_future = loop.create_future()
        await self.send_set_unit2_target_temperature_command(target_temperature)
        return await self._set_unit2_result_future


def print_fridge_data(data: FridgeData):
    info = {
        'on': data.powered_on,
        'runMode': data.run_mode.name,
        'lowVoltageLevel': data.battery_saver.name,
        'batteryVoltage': data.battery_voltage,
        'batteryChargePercent': data.battery_charge_percent,
        'temperatureUnit': data.temperature_unit.name,
        'units': {
        }
    }

    if data.unit1 is not None:
        info['units']['1'] = {
            'temperature': data.unit1.current_temperature,
            'target': data.unit1.target_temperature
        }
    
    if data.unit2 is not None:
        info['units']['2'] = {
            'temperature': data.unit2.current_temperature,
            'target': data.unit2.target_temperature
        }

    print(json.dumps(info))


async def run(addr: str, bind: bool, poll: bool, pollinterval: int):
    async with Fridge(addr) as fridge:
        if bind:
            await asyncio.wait_for(fridge.bind(), 30)

        fridge.on_query_response = print_fridge_data

        try:
            await asyncio.wait_for(fridge.query(), 5)
        except TimeoutError:
            pass

        while poll:
            await asyncio.sleep(pollinterval)

            try:
                await asyncio.wait_for(fridge.query(), 5)
            except TimeoutError:
                pass


def main():
    parser = argparse.ArgumentParser(
        prog='fridge.py',
        description='Fridge monitor for Alpicool / Brass Monkey fridges'
    )

    parser.add_argument('address', help='Bluetooth address of fridge')
    parser.add_argument('-b', '--bind', action='store_true', help='Press settings button on fridge to confirm fridge selection')
    parser.add_argument('-l', '--loop', action='store_true', help='Poll at regular intervals (default: query once)')
    parser.add_argument('-t', '--pollinterval', type = int, default=10, help='Poll interval in seconds (default: 10)')

    args = parser.parse_args()

    logging.basicConfig()

    try:
        asyncio.run(run(args.address, args.bind, args.loop, args.pollinterval))
    except KeyboardInterrupt:
        sys.stderr.write('Exiting\n')
        return

if __name__ == '__main__':
    main()

