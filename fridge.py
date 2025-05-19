#!/usr/bin/env python3

'''fridge.py

Monitors an Alpicool compatible fridge
'''

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

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic


SERVICE_UID = '1234'
SERVICE_UUID = '00001234-0000-1000-8000-00805f9b34fb'
COMMAND_UID = '1235'
COMMAND_UUID = '00001235-0000-1000-8000-00805f9b34fb'
NOTIFY_UID = '1236'
NOTIFY_UUID = '00001236-0000-1000-8000-00805f9b34fb'

logger = logging.getLogger(__name__)


class FridgeCommand(int, Enum):
    '''Fridge command codes'''
    # pylint: disable=invalid-name
    Bind = 0
    Query = 1
    Set = 2
    Reset = 4
    SetUnit1Target = 5
    SetUnit2Target = 6


class FridgeRunMode(int, Enum):
    '''Fridge run mode'''
    # pylint: disable=invalid-name
    Max = 0
    Eco = 1


class FridgeBatterySaver(int, Enum):
    '''Fridge low voltage cutout level'''
    # pylint: disable=invalid-name
    Low = 0
    Mid = 1
    High = 2


class FridgeTemperatureUnit(int, Enum):
    '''Fridge temperature unit'''
    # pylint: disable=invalid-name
    Celsius = 0
    Fahrenheit = 1
    Farenheit = 1


@dataclass
class FridgeUnitData:
    '''Data for a single fridge unit'''
    # pylint: disable=too-many-instance-attributes
    target_temperature: int
    hysteresis: int
    temperature_correction_hot: int
    temperature_correction_mid: int
    temperature_correction_cold: int
    temperature_correction_halt: int
    current_temperature: int


@dataclass
class FridgeData:
    '''Fridge data'''
    # pylint: disable=too-many-instance-attributes
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

    def to_dict(self) -> dict:
        '''Converts fridge data to json dict'''
        info = {
            'on': self.powered_on,
            'runMode': self.run_mode.name,
            'lowVoltageLevel': self.battery_saver.name,
            'batteryVoltage': self.battery_voltage,
            'batteryChargePercent': self.battery_charge_percent,
            'temperatureUnit': self.temperature_unit.name,
            'units': {
            }
        }

        if self.unit1 is not None:
            info['units']['1'] = {
                'temperature': self.unit1.current_temperature,
                'target': self.unit1.target_temperature
            }

        if self.unit2 is not None:
            info['units']['2'] = {
                'temperature': self.unit2.current_temperature,
                'target': self.unit2.target_temperature
            }

        return info



def decode_unit1_data(data: Union[bytes, bytearray]) -> FridgeUnitData:
    '''Decode the data for unit 1 from packet data'''
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
    '''Decode the data for unit 2 from packet data'''
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
    '''Decode fridge data from packet data'''
    if len(data) < 18:
        raise ValueError('Packet too short')

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


def create_packet(data: Union[bytes, bytearray]) -> bytes:
    '''Create a packet for sending to a fridge'''
    pkt = b'\xFE\xFE' + struct.pack('B', len(data) + 2) + data
    pkt += struct.pack('>H', sum(int(v) for v in pkt))
    return pkt


def get_packet_data(data: Union[bytes, bytearray]) -> bytes:
    '''Extract the data from a packet'''

    if len(data) <= 2:
        logger.warning(
            'Packet is too small: %d bytes',
            len(data),
            extra={ 'data': data.hex() }
        )
        return None

    if data[:2] != b'\xFE\xFE':
        logger.warning(
            'Invalid frame header: %s',
            data[:2].hex(),
            extra={ 'data': data.hex() }
        )
        return None

    pktlen = struct.unpack_from('B', data, 2)[0]

    if pktlen != len(data) - 3:
        logger.warning(
            'Content length does not match: %d != %d',
            len(data) - 3,
            pktlen,
            extra={ 'data': data.hex() }
        )
        return None

    csum = struct.unpack_from('>H', data[-2:])[0]
    calcsum = sum(int(v) for v in data[:-2])

    if csum not in (calcsum, calcsum * 2):
        logger.warning(
            'Invalid checksum: %04X != %04X',
            calcsum,
            csum,
            extra={ 'data': data.hex() }
        )
        return None

    return data[3:-2]


def encode_bind_command() -> bytes:
    '''Encode a Bind command'''
    return create_packet(struct.pack('B', FridgeCommand.Bind))


def encode_query_command() -> bytes:
    '''Encode a Query command'''
    return create_packet(struct.pack('B', FridgeCommand.Query))


def encode_set_command(data: FridgeData) -> bytes:
    '''Encode a Set command'''
    # pylint: disable=no-else-return
    if data.unit2 is None:
        return create_packet(struct.pack(
            '>B??BBbbbbBBbbbb',
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
            '>B??BBbbbbBBbbbbbxxbbbbbxxx',
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
    '''Encode a Reset command'''
    return create_packet(struct.pack('B', FridgeCommand.Reset))


def encode_set_unit1_target_command(temp: int) -> bytes:
    '''Encode a Set Unit 1 Target Temperature command'''
    return create_packet(struct.pack('Bb', FridgeCommand.SetUnit1Target, temp))


def encode_set_unit2_target_command(temp: int) -> bytes:
    '''Encode a Set Unit 2 Target Temperature command'''
    return create_packet(struct.pack('Bb', FridgeCommand.SetUnit2Target, temp))


class Fridge:
    '''Fridge communication class'''
    # pylint: disable=too-many-instance-attributes
    on_query_response: Optional[Callable[[FridgeData], Any]] = None

    command_characteristic: Optional[BleakGATTCharacteristic] = None
    notify_characteristic: Optional[BleakGATTCharacteristic] = None
    client: BleakClient = None
    verbose: bool = False

    _bind_result_future: Optional[Future[int]] = None
    _query_result_future: Optional[Future[FridgeData]] = None
    _set_result_future: Optional[Future[FridgeData]] = None
    _reset_result_future: Optional[Future[FridgeData]] = None
    _set_unit1_result_future: Optional[Future[FridgeData]] = None
    _set_unit2_result_future: Optional[Future[FridgeData]] = None

    def __init__(self, client: Union[BleakClient, BLEDevice, str], verbose: bool):
        if isinstance(client, BleakClient):
            self.client = client
        else:
            self.client = BleakClient(client)

        self.verbose = verbose

    async def connect(self):
        '''Connect to the BLE fridge'''
        for _ in range(0, 2):
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

        for _, c in self.client.services.characteristics.items():
            if c.service_uuid in (SERVICE_UID, SERVICE_UUID):
                if c.uuid in (COMMAND_UID, COMMAND_UUID):
                    self.command_characteristic = c
                elif c.uuid in (NOTIFY_UID, NOTIFY_UUID):
                    self.notify_characteristic = c

        if self.command_characteristic is None or self.notify_characteristic is None:
            self.disconnect()
            raise ValueError('Required GATT characteristics not found')

        await self.client.start_notify(self.notify_characteristic, self._notify_callback)

    async def disconnect(self):
        '''Disconnect from the BLE fridge'''
        await self.client.disconnect()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.disconnect()

    def _notify_callback(self, sender: BleakGATTCharacteristic, pkt: bytearray):
        '''Callback for BLE notify'''

        if self.verbose:
            sys.stderr.write(f'Recv: {sender}: {pkt}\n')
        
        # pylint: disable=unused-argument
        data = get_packet_data(pkt)

        if data is None or len(data) < 2:
            return

        cmd = struct.unpack_from('B', data, 0)[0]

        if cmd == FridgeCommand.Bind:
            self._notify_bind(struct.unpack_from('B', data, 1)[0])
        elif cmd == FridgeCommand.Query:
            self._notify_query(decode_fridge_data(data[1:]))
        elif cmd == FridgeCommand.Set:
            self._notify_set(decode_fridge_data(data[1:]))
        elif cmd == FridgeCommand.Reset:
            self._notify_reset(decode_fridge_data(data[1:]))
        elif cmd == FridgeCommand.SetUnit1Target:
            self._notify_set_unit1_target_temperature(struct.unpack_from('b', data, 1)[0])
        elif cmd == FridgeCommand.SetUnit2Target:
            self._notify_set_unit2_target_temperature(struct.unpack_from('b', data, 1)[0])

    def _notify_bind(self, data: int):
        '''Callback for Bind response'''
        if isinstance(self._bind_result_future, Future):
            self._bind_result_future.set_result(data)

    def _notify_query(self, data: FridgeData):
        '''Callback for Query response'''
        if self.on_query_response is not None:
            self.on_query_response(data)

        if isinstance(self._query_result_future, Future):
            self._query_result_future.set_result(data)

    def _notify_set(self, data: FridgeData):
        '''Callback for Set response'''
        if isinstance(self._set_result_future, Future):
            self._set_result_future.set_result(data)

    def _notify_reset(self, data: FridgeData):
        '''Callback for Reset response'''
        if isinstance(self._reset_result_future, Future):
            self._reset_result_future.set_result(data)

    def _notify_set_unit1_target_temperature(self, data: int):
        '''Callback for Set Unit 1 Target Temperature response'''
        if isinstance(self._set_unit1_result_future, Future):
            self._set_unit1_result_future.set_result(data)

    def _notify_set_unit2_target_temperature(self, data: int):
        '''Callback for Set Unit 2 Target Temperature response'''
        if isinstance(self._set_unit2_result_future, Future):
            self._set_unit2_result_future.set_result(data)

    async def _send_command(self, pkt: bytes):
        '''Send a command to the BLE fridge'''

        if self.verbose:
            sys.stderr.write(f'Send: {pkt}\n')
        
        await self.client.write_gatt_char(self.command_characteristic, pkt, response = True)

    async def _send_bind_command(self):
        '''Send a Bind command'''
        pkt = encode_bind_command()
        await self._send_command(pkt)

    async def _send_query_command(self):
        '''Send a Query command'''
        pkt = encode_query_command()
        await self._send_command(pkt)

    async def _send_set_command(self, data: FridgeData):
        '''Send a Set command'''
        pkt = encode_set_command(data)
        await self._send_command(pkt)

    async def _send_reset_command(self):
        '''Send a Reset command'''
        pkt = encode_reset_command()
        await self._send_command(pkt)

    async def _send_set_unit1_target_temperature_command(self, target_temperature: int):
        '''Send a Set Unit 1 Target Temperature command'''
        pkt = encode_set_unit1_target_command(target_temperature)
        await self._send_command(pkt)

    async def _send_set_unit2_target_temperature_command(self, target_temperature: int):
        '''Send a Set Unit 2 Target Temperature command'''
        pkt = encode_set_unit2_target_command(target_temperature)
        await self._send_command(pkt)

    async def bind(self) -> int:
        '''Send a Bind command and await its response'''
        loop = asyncio.get_event_loop()
        self._bind_result_future = loop.create_future()
        await self._send_bind_command()
        return await self._bind_result_future

    async def query(self) -> FridgeData:
        '''Send a Query command and await its response'''
        loop = asyncio.get_event_loop()
        self._query_result_future = loop.create_future()
        await self._send_query_command()
        return await self._query_result_future

    async def set(self, data: FridgeData) -> FridgeData:
        '''Send a Set command and await its response'''
        loop = asyncio.get_event_loop()
        self._set_result_future = loop.create_future()
        await self._send_set_command(data)
        return await self._set_result_future

    async def reset(self) -> FridgeData:
        '''Send a Reset command and await its response'''
        loop = asyncio.get_event_loop()
        self._reset_result_future = loop.create_future()
        await self._send_reset_command()
        return await self._reset_result_future

    async def set_unit1_target_temperature(self, target_temperature: int) -> int:
        '''Send a Set Unit 1 Target Temperature command and await its response'''
        loop = asyncio.get_event_loop()
        self._set_unit1_result_future = loop.create_future()
        await self._send_set_unit1_target_temperature_command(target_temperature)
        return await self._set_unit1_result_future

    async def set_unit2_target_temperature(self, target_temperature: int) -> int:
        '''Send a Set Unit 2 Target Temperature command and await its response'''
        loop = asyncio.get_event_loop()
        self._set_unit2_result_future = loop.create_future()
        await self._send_set_unit2_target_temperature_command(target_temperature)
        return await self._set_unit2_result_future


def print_fridge_data(data: FridgeData):
    '''Dump a JSON representation of the fridge data to standard output'''
    print(json.dumps(data.to_dict()))


async def run(addr: str, bind: bool, poll: bool, pollinterval: int, verbose: bool):
    '''Run the write-notify loop'''
    while True:
        fridge_dev = await BleakScanner.find_device_by_address(addr)

        if fridge_dev is None:
            logger.info('Fridge BLE device not found')
            continue

        logger.info('Fridge BLE device found - attempting to connect')

        async with Fridge(fridge_dev, verbose) as fridge:
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
    '''fridge.py entry point when run as a script'''
    parser = argparse.ArgumentParser(
        prog='fridge.py',
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
        '-v',
        '--verbose',
        action='store_true',
        help='Write command and notification frames to the console'
    )
    parser.add_argument(
        '-t',
        '--pollinterval',
        type = int,
        default=10,
        help='Poll interval in seconds (default: 10)'
    )

    args = parser.parse_args()

    logging.basicConfig()

    try:
        asyncio.run(run(args.address, args.bind, args.loop, args.pollinterval, args.verbose))
    except KeyboardInterrupt:
        sys.stderr.write('Exiting\n')


if __name__ == '__main__':
    main()
