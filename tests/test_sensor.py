import asyncio
from contextlib import redirect_stdout
import io
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from power_saving import PowerSavingResult
from sensor import decode_measurement, main


class DecodeMeasurementTests(unittest.TestCase):
    def test_normal_measurement(self):
        self.assertEqual(decode_measurement(struct.pack("<hBH", 2480, 48, 2950)), (24.8, 48, 2.95))

    def test_negative_temperature(self):
        self.assertEqual(decode_measurement(struct.pack("<hBH", -525, 75, 2600)), (-5.25, 75, 2.6))

    def test_invalid_lengths(self):
        for data in (b"", b"\x00" * 4, b"\x00" * 6):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_measurement(data)


class ConsolePowerTests(unittest.IsolatedAsyncioTestCase):
    async def run_reader(self, *, power_requested, send_sample=True):
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        closed = []

        class Client:
            def __init__(self, _device, disconnected_callback, timeout):
                self.callback = disconnected_callback
                self.services = SimpleNamespace(get_characteristic=lambda _uuid: object())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                closed.append(True)

            async def start_notify(self, _uuid, callback):
                if send_sample:
                    callback(None, struct.pack("<hBH", 2480, 48, 2950))
                    if power_requested:
                        asyncio.get_running_loop().call_soon(self.callback, self)

        try:
            with (
                redirect_stdout(io.StringIO()),
                patch("sensor.BleakScanner.find_device_by_address", new=AsyncMock(return_value=device)),
                patch("sensor.BleakClient", Client),
                patch("sensor.request_low_power", new=AsyncMock(return_value=PowerSavingResult(power_requested, "test"))),
                patch("sensor.FIRST_MEASUREMENT_TIMEOUT", 0.01),
            ):
                await main("AA")
        finally:
            self.assertEqual(closed, [True])

    async def test_unavailable_power_mode_reads_once_and_closes(self):
        await self.run_reader(power_requested=False)

    async def test_supported_power_mode_closes_on_disconnection(self):
        await self.run_reader(power_requested=True)

    async def test_silent_sensor_is_not_held_forever(self):
        with self.assertRaises(asyncio.TimeoutError):
            await self.run_reader(power_requested=True, send_sample=False)


if __name__ == "__main__":
    unittest.main()
