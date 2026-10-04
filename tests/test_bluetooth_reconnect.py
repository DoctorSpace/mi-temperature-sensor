import asyncio
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bleak.exc import BleakError

from power_saving import PowerSavingResult
from widget import BluetoothWorker, DEVICE_DISCOVERY_TIMEOUT, SessionResult


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_periodic_reads_reuse_the_discovered_device_without_rescanning(self):
        worker = BluetoothWorker("AA", mode="periodic")
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        opened = []
        closed = []

        class Client:
            def __init__(self, current_device, **_kwargs):
                opened.append(current_device)
                self.services = SimpleNamespace(get_characteristic=lambda _uuid: object())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                closed.append(True)

            async def start_notify(self, _uuid, callback):
                callback(None, struct.pack("<hBH", 2480, 48, 2950))

        with (
            patch("widget.BleakScanner.find_device_by_address", new=AsyncMock(return_value=device)) as scanner,
            patch("widget.BleakClient", Client),
            patch("widget.request_low_power", new=AsyncMock(return_value=PowerSavingResult(True, "ack"))),
        ):
            first = await worker.connect_once()
            second = await worker.connect_once()
        scanner.assert_awaited_once_with("AA", timeout=DEVICE_DISCOVERY_TIMEOUT)
        self.assertEqual(opened, [device, device])
        self.assertEqual(closed, [True, True])
        self.assertTrue(first.scheduled and second.scheduled)
        self.assertIs(worker.cached_device, device)

    async def test_manual_restart_can_use_a_previously_discovered_device(self):
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        worker = BluetoothWorker("aa", cached_device=device)
        with (
            patch("widget.BleakScanner.find_device_by_address", new=AsyncMock()) as scanner,
            patch.object(worker, "read_device", new=AsyncMock(return_value=SessionResult(1, 1, True))) as reader,
        ):
            await worker.connect_once()
        scanner.assert_not_awaited()
        reader.assert_awaited_once_with(device)

    async def test_failed_cached_connection_is_forgotten_and_rediscovered(self):
        old = SimpleNamespace(name="LYWSD03MMC", address="AA")
        fresh = SimpleNamespace(name="LYWSD03MMC", address="AA")
        worker = BluetoothWorker("AA", cached_device=old)
        invalidations = []
        worker.device_cached.connect(invalidations.append)
        with patch.object(worker, "read_device", new=AsyncMock(side_effect=BleakError("offline"))):
            with self.assertRaisesRegex(BleakError, "offline"):
                await worker.connect_once()
        self.assertIsNone(worker.cached_device)
        self.assertEqual(invalidations, [None])
        with (
            patch("widget.BleakScanner.find_device_by_address", new=AsyncMock(return_value=fresh)) as scanner,
            patch.object(worker, "read_device", new=AsyncMock(return_value=SessionResult(1, 1, True))) as reader,
        ):
            await worker.connect_once()
        scanner.assert_awaited_once_with("AA", timeout=30)
        reader.assert_awaited_once_with(fresh)

    async def test_address_change_does_not_reuse_another_sensor(self):
        device = SimpleNamespace(name="LYWSD03MMC", address="OLD")
        worker = BluetoothWorker("NEW", cached_device=device)
        self.assertIsNone(worker.cached_device)

    async def test_changing_an_existing_workers_address_invalidates_the_cache(self):
        device = SimpleNamespace(name="LYWSD03MMC", address="OLD")
        worker = BluetoothWorker("OLD", cached_device=device)
        worker.address = "NEW"
        with patch("widget.BleakScanner.find_device_by_address", new=AsyncMock(return_value=None)) as scanner:
            with self.assertRaisesRegex(BleakError, "30 секунд"):
                await worker.connect_once()
        self.assertIsNone(worker.cached_device)
        scanner.assert_awaited_once_with("NEW", timeout=30)

    async def test_cancelling_for_manual_restart_keeps_the_known_device(self):
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        worker = BluetoothWorker("AA", cached_device=device)
        with patch.object(worker, "read_device", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await worker.connect_once()
        self.assertIs(worker.cached_device, device)

    async def test_a_connection_without_any_measurements_is_not_cached(self):
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        worker = BluetoothWorker("AA", cached_device=device)
        with patch.object(worker, "read_device", new=AsyncMock(return_value=SessionResult())):
            await worker.connect_once()
        self.assertIsNone(worker.cached_device)


if __name__ == "__main__":
    unittest.main()
