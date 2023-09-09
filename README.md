Fridge monitor for Alpicool / Brass Monkey fridges

I created this primarily to log the temperature of my fridge.

## Usage
positional arguments:

address
    : Bluetooth address of fridge

options:

-b, --bind
    : Press settings button on fridge to confirm fridge selection

-l, --loop
    : Poll at regular intervals (default: query once)

-t POLLINTERVAL, --pollinterval POLLINTERVAL
    : Poll interval in seconds (default: 10)

## Requirements

This script uses [bleak](https://github.com/hbldh/bleak) as its bluetooth library.

As this script uses bluetooth, you will need a working bluetooth adaptor.

## Technical

This script was put together after extracting the javascript from version 2.0.0 of the Alpicool CAR FRIDGE FREEZER android app (the current 2.2.9 version has the javascript compiled into Hermes bytecode).

The app enables notifications on characteristic 0x1236 of GATT service 0x1234, and sends commands on characteristic 0x1235 of GATT service 0x1234

Note that there is no authentication

* Pairing to the device is not required
* When pairing no pin is required
* The "bind" command is a convenience function - i.e. the fridge still accepts commands even if the "bind" command has never been issued or acknowledged

Connecting to the BLE device locks out all other clients and disables BLE advertisements until the client disconnects (though it does seem to show up after a while on the next bluetooth address as a bluetooth headset `JR-bluetooth`)

My fridge has a bluetooth device name of `WT-0001`, and the app also looks for bluetooth device names starting with `A1-`, `AK1-`, `AK2-`, or `AK3-`

### Command and notification structure:

Offset  | Length  | Description
--------|---------|------------
0x00    | 2       | Frame header (FE FE)
0x02    | 1       | Data length
0x03    | 1       | Command / Response code
0x04    | len - 3 | Command / Response data
len - 2 | 2       | Checksum (big-endian sum of all other bytes)

### Command / Response codes:

Code | Name | Description
-----|------|------------
0x00 | bind | Bind (Fridge displays APP and sends a bind response after settings button is pressed)
0x01 | query | Query
0x02 | setOther | Set
0x04 | reset | Reset
0x05 | setLeft | Set Unit 1 (Left) Target
0x06 | setRight | Set Unit 2 (Right) Target

### Fridge Query Data structure:

All temperature values are signed 8-bit integers represented in the selected temperature unit

Offset | Name | Description
-------|------|------------
0x00   | locked | Fridge controls locked (boolean)
0x01   | poweredOn | Fridge switched on (boolean)
0x02   | runMode | Fridge running mode (Max, Eco)
0x03   | batSaver | Low voltage cutout level (Low, Mid, High)
0x04   | leftTarget | Unit 1 (Left) target temperature
0x05   | tempMax | Maximum selectable temperature
0x06   | tempMin | Minimum selectable temperature
0x07   | leftRetDiff | Unit 1 (Left) hysteresis
0x08   | startDelay | Start Delay (minutes)
0x09   | unit | Temperature unit (Celsius, Farenheit)
0x0A   | leftTCHot | Unit 1 (Left) temperature correction when at or above -6°C
0x0B   | leftTCMid | Unit 1 (Left) temperature correction when between -12°C and -6°C
0x0C   | leftTCCold | Unit 1 (Left) temperature correction when below -12°C
0x0D   | leftTCHalt | Unit 1 (Left) temperature correction when shut down
0x0E   | leftCurrent | Unit 1 (Left) current temperature
0x0F   | batPercent | Battery charge level in percent (likely voltage-based) (can be 0x7f if unknown)
0x10   | batVolInt | Integer portion of battery voltage
0x11   | batVolDec | Decimal portion of battery voltage

For dual-zone fridges:

Offset | Name | Description
-------|------|------------
0x12   | rightTarget | Unit 2 (Right) target temperature
0x13   | | Not used
0x14   | | Not used
0x15   | rightRetDiff | Unit 2 (Right) hysteresis
0x16   | rightTCHot | Unit 2 (Right) temperature correction when at or above -6°C
0x17   | rightTCMid | Unit 2 (Right) temperature correction when between -12°C and -6°C
0x18   | rightTCCold | Unit 2 (Right) temperature correction when below -12°C
0x19   | rightTCHalt | Unit 2 (Right) temperature correction when shut down
0x1A   | rightCurrent | Unit 2 (Right) current temperature
0x1B   | runningStatus | Running Status (Unknown - I don't have a dual-zone fridge to test with)

### Fridge Set Data structure

All temperature values are signed 8-bit integers represented in the selected temperature unit

Offset | Name | Description
-------|------|------------
0x00   | locked | Fridge controls locked (boolean)
0x01   | poweredOn | Fridge switched on (boolean)
0x02   | runMode | Fridge running mode (Max, Eco)
0x03   | batSaver | Low voltage cutout level (Low, Mid, High)
0x04   | leftTarget | Unit 1 (Left) target temperature
0x05   | tempMax | Maximum selectable temperature
0x06   | tempMin | Minimum selectable temperature
0x07   | leftRetDiff | Unit 1 (Left) hysteresis
0x08   | startDelay | Start Delay (minutes)
0x09   | unit | Temperature unit (Celsius, Farenheit)
0x0A   | leftTCHot | Unit 1 (Left) temperature correction when at or above -6°C
0x0B   | leftTCMid | Unit 1 (Left) temperature correction when between -12°C and -6°C
0x0C   | leftTCCold | Unit 1 (Left) temperature correction when below -12°C
0x0D   | leftTCHalt | Unit 1 (Left) temperature correction when shut down

For dual-zone fridges:

Offset | Name | Description
-------|------|------------
0x0E   | rightTarget | Unit 2 (Right) target temperature
0x0F   | | Always zero
0x10   | | Always zero
0x11   | rightRetDiff | Unit 2 (Right) hysteresis
0x12   | rightTCHot | Unit 2 (Right) temperature correction when at or above -6°C
0x13   | rightTCMid | Unit 2 (Right) temperature correction when between -12°C and -6°C
0x14   | rightTCCold | Unit 2 (Right) temperature correction when below -12°C
0x15   | rightTCHalt | Unit 2 (Right) temperature correction when shut down
0x16   | | Always zero
0x17   | | Always zero
0x18   | | Always zero

### App Command flow

When connecting to a fridge, the app sends a bind command and waits for a bind notify message to confirm that you are connecting to the right fridge.

```
2023-09-05 23:05:12.827173  0x1235  Write   fe fe 03 00 01 ff
2023-09-05 23:05:14.276572  0x1236  Notify  fe fe 04 00 01 02 01
```

After the fridge selection has been confirmed, the app sends a query command every 2 seconds.

```
2023-09-05 23:05:16.358755  0x1235  Write   fe fe 03 01 02 00
2023-09-05 23:05:16.455384  0x1236  Notify  fe fe 15 01 00 01 00 00 f1 14 ec 02 00 00 00 00 00 00 f3 64 0c 03 05 6c
2023-09-05 23:05:18.383106  0x1235  Write   fe fe 03 01 02 00
2023-09-05 23:05:18.466445  0x1236  Notify  fe fe 15 01 00 01 00 00 f1 14 ec 02 00 00 00 00 00 00 f3 64 0c 03 05 6c
```

Setting the target temperature in the app sends the Set Left/Right Target command with the temperature in the selected unit.

```
2023-09-05 19:49:20.487666  0x1235  Write   fe fe 03 05 ec 02 f1
2023-09-05 19:49:20.570163  0x1236  Notify  fe fe 03 05 ec 02 f1
```

Setting anything else sends a Set command with all of the unchanged values filled in from the previous Query data.

```
2023-09-05 19:54:14.152600  0x1236  Notify  fe fe 15 01 00 01 00 00 ec 14 ec 02 00 00 00 00 00 00 f7 7f 0b 01 05 83
2023-09-05 19:54:15.444012  0x1235  Write   fe fe 11 02 00 01 00 02 ec 14 ec 02 00 00 00 00 00 00 04 00
2023-09-05 19:54:15.530186  0x1236  Notify  fe fe 15 02 00 01 00 02 ec 14 ec 02 00 00 00 00 00 00 f7 7f 0b 01 05 86
2023-09-05 19:54:15.991368  0x1235  Write   fe fe 03 01 02 00
2023-09-05 19:54:16.071697  0x1236  Notify  fe fe 15 01 00 01 00 02 ec 14 ec 02 00 00 00 00 00 00 f7 7f 0b 01 05 85
```
