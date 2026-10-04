"""Read live measurements from a stock Xiaomi LYWSD03MMC on Windows."""

import argparse
import asyncio
from datetime import datetime
import struct

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from power_saving import FIRST_MEASUREMENT_TIMEOUT, request_low_power


MEASUREMENTS_UUID = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"


def decode_measurement(data):
    """Return temperature (C), humidity (%) and battery voltage (V)."""
    if len(data) != 5:
        raise ValueError(f"Unexpected measurement packet: {data.hex()}")
    temperature, humidity, millivolts = struct.unpack("<hBH", data)
    return temperature / 100, humidity, millivolts / 1000


def show_measurement(_characteristic, data):
    if len(data) != 5:
        print(f"Unexpected packet: {data.hex()}")
        return
    temperature, humidity, voltage = decode_measurement(data)
    print(
        f"{datetime.now():%H:%M:%S} | "
        f"Temperature: {temperature:.2f} C | "
        f"Humidity: {humidity}% | Battery: {voltage:.3f} V",
        flush=True,
    )


async def main(address):
    print("Searching for the sensor (10 seconds)...", flush=True)
    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=10)
    else:
        devices = await BleakScanner.discover(timeout=10)
        matches = [d for d in devices if "LYWSD03MMC" in (d.name or "").upper()]
        if len(matches) > 1:
            print("Several sensors found. Select one using --address:")
            for device in matches:
                print(f"  {device.name}: {device.address}")
            return
        device = matches[0] if matches else None

    if device is None:
        print("Sensor not found. Move it closer and disconnect Mi Home.")
        print("If it has custom firmware, this stock-firmware reader may not work.")
        return

    print(f"Connecting to {device.name} ({device.address})...", flush=True)
    disconnected = asyncio.Event()
    received = asyncio.Event()

    def notify(characteristic, data):
        show_measurement(characteristic, data)
        if len(data) == 5:
            received.set()

    async def wait_for_sample():
        while not received.is_set():
            if disconnected.is_set():
                raise BleakError("Sensor disconnected before a measurement arrived")
            await asyncio.sleep(0.1)

    async with BleakClient(
        device, disconnected_callback=lambda _client: disconnected.set(), timeout=30
    ) as client:
        if client.services.get_characteristic(MEASUREMENTS_UUID) is None:
            print("Measurement characteristic not found.")
            print("This firmware may require a different reading method.")
            return
        await client.start_notify(MEASUREMENTS_UUID, notify)
        power = await request_low_power(client)
        print(power.details, flush=True)
        if not power.requested:
            print("Power saving unavailable: read once, then disconnect to avoid a permanent high-power connection.")
        await asyncio.wait_for(wait_for_sample(), timeout=FIRST_MEASUREMENT_TIMEOUT)
        if not power.requested:
            return
        print("Waiting for measurements. Press Ctrl+C to stop.", flush=True)
        while not disconnected.is_set():
            try:
                await asyncio.wait_for(disconnected.wait(), timeout=60)
            except asyncio.TimeoutError:
                print("Still connected; waiting for notifications...", flush=True)
        print("Sensor disconnected. Run the script again to reconnect.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="Bluetooth address of a specific sensor")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.address))
    except KeyboardInterrupt:
        print("\nStopped.")
    except (BleakError, OSError, asyncio.TimeoutError) as error:
        print(f"Bluetooth error: {error}")
        print("Check Bluetooth, close Mi Home, and move the sensor closer.")
