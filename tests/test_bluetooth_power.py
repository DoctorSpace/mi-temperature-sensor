import asyncio
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bleak.exc import BleakError

from power_saving import CONNECTION_INTERVAL_UUID, PowerSavingResult
from sensor import MEASUREMENTS_UUID
from widget import BluetoothWorker, SessionResult


class BluetoothPowerTests(unittest.IsolatedAsyncioTestCase):
    async def session(self, *, mode="continuous", supports_power=True, write_error=False):
        worker = BluetoothWorker("AA", mode=mode)
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        operations = []

        class Client:
            def __init__(self, _device, disconnected_callback, timeout):
                self.disconnect = disconnected_callback
                self.services = SimpleNamespace(get_characteristic=self.characteristic)

            def characteristic(self, uuid):
                operations.append(("lookup", uuid))
                if uuid == CONNECTION_INTERVAL_UUID and not supports_power:
                    return None
                return SimpleNamespace(uuid=uuid, properties=["write"])

            async def __aenter__(self):
                operations.append(("connect",))
                return self

            async def __aexit__(self, *_args):
                operations.append(("disconnect",))

            async def start_notify(self, uuid, callback):
                operations.append(("notify", uuid))
                callback(None, struct.pack("<hBH", 2480, 48, 2950))

            async def write_gatt_char(self, characteristic, payload, response):
                operations.append(("write", characteristic.uuid, payload, response))
                if write_error:
                    raise BleakError("refused")
                if mode == "continuous":
                    asyncio.get_running_loop().call_soon(self.disconnect, self)

        with (
            patch("widget.BleakScanner.find_device_by_address", new=AsyncMock(return_value=device)),
            patch("widget.BleakClient", Client),
        ):
            result = await worker.connect_once()
        return worker, result, operations

    async def test_power_request_is_after_subscription_and_last_gatt_operation(self):
        _worker, result, operations = await self.session()
        self.assertFalse(result.scheduled)
        notify_index = operations.index(("notify", MEASUREMENTS_UUID))
        write_index = next(i for i, operation in enumerate(operations) if operation[0] == "write")
        self.assertGreater(write_index, notify_index)
        self.assertEqual(operations[write_index + 1:], [("disconnect",)])

    async def test_periodic_mode_disconnects_after_one_measurement(self):
        worker, result, operations = await self.session(mode="periodic")
        self.assertTrue(result.scheduled)
        self.assertEqual(result.measurements, 1)
        self.assertEqual(operations[-1], ("disconnect",))
        self.assertEqual(len(worker.disconnects), 0)

    async def test_missing_power_characteristic_falls_back_without_permanent_connection(self):
        worker, result, operations = await self.session(supports_power=False)
        self.assertTrue(result.scheduled)
        self.assertFalse(any(operation[0] == "write" for operation in operations))
        self.assertIn("не поддерживается", worker.power_details)

    async def test_refused_power_request_falls_back_instead_of_reconnecting_immediately(self):
        _worker, result, operations = await self.session(write_error=True)
        self.assertTrue(result.scheduled)
        self.assertEqual(operations[-1], ("disconnect",))

    async def cooldowns(self, result):
        worker = BluetoothWorker()
        delays = []

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 2:
                worker.stop_requested.set()

        with (
            patch.object(worker, "connect_once", new=AsyncMock(return_value=result)),
            patch("widget.asyncio.sleep", new=sleep),
        ):
            await worker.listen()
        return delays

    async def test_one_packet_on_short_connection_does_not_reset_backoff(self):
        self.assertEqual(await self.cooldowns(SessionResult(1, 1)), [15, 30])

    async def test_long_stable_session_resets_backoff(self):
        self.assertEqual(await self.cooldowns(SessionResult(10, 200)), [15, 15])

    async def test_scheduled_reads_wait_the_full_interval(self):
        self.assertEqual(await self.cooldowns(SessionResult(1, 1, scheduled=True)), [300, 300])

    async def test_cancel_closes_the_connection(self):
        worker = BluetoothWorker("AA")
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        subscribed = asyncio.Event()
        closed = []

        class Client:
            def __init__(self, *_args, **_kwargs):
                self.services = SimpleNamespace(get_characteristic=lambda _uuid: object())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                closed.append(True)

            async def start_notify(self, _uuid, _callback):
                subscribed.set()

        with (
            patch("widget.BleakScanner.find_device_by_address", new=AsyncMock(return_value=device)),
            patch("widget.BleakClient", Client),
            patch("widget.request_low_power", new=AsyncMock(return_value=PowerSavingResult(True, "ack"))),
        ):
            task = asyncio.create_task(worker.listen())
            await asyncio.wait_for(subscribed.wait(), timeout=2)
            worker.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(closed, [True])

    async def test_missing_first_packet_does_not_keep_a_high_rate_link_open(self):
        worker = BluetoothWorker("AA", mode="periodic")
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        closed = []

        class Client:
            def __init__(self, *_args, **_kwargs):
                self.services = SimpleNamespace(get_characteristic=lambda _uuid: object())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                closed.append(True)

            async def start_notify(self, _uuid, _callback):
                pass

        with (
            patch("widget.BleakScanner.find_device_by_address", new=AsyncMock(return_value=device)),
            patch("widget.BleakClient", Client),
            patch("widget.request_low_power", new=AsyncMock(return_value=PowerSavingResult(False, "unsupported"))),
            patch("widget.FIRST_MEASUREMENT_TIMEOUT", -1),
        ):
            with self.assertRaisesRegex(TimeoutError, "Первое измерение"):
                await worker.connect_once()
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
