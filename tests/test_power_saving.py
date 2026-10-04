import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from bleak.exc import BleakError

from power_saving import (
    CONNECTION_INTERVAL_UUID, LOW_POWER_REQUEST, ReconnectBackoff, request_low_power,
)


class PowerRequestTests(unittest.IsolatedAsyncioTestCase):
    def client(self, characteristic):
        return SimpleNamespace(
            services=SimpleNamespace(get_characteristic=lambda uuid: characteristic),
            write_gatt_char=AsyncMock(),
        )

    async def test_uses_known_uuid_payload_and_acknowledged_write(self):
        queried = []
        characteristic = SimpleNamespace(properties=["write"], uuid=CONNECTION_INTERVAL_UUID)
        client = self.client(characteristic)
        client.services.get_characteristic = lambda uuid: queried.append(uuid) or characteristic
        result = await request_low_power(client)
        self.assertTrue(result.requested)
        self.assertEqual(queried, [CONNECTION_INTERVAL_UUID])
        client.write_gatt_char.assert_awaited_once_with(characteristic, b"\xf4\x01\x00", response=True)
        self.assertEqual(LOW_POWER_REQUEST.hex(), "f40100")
        self.assertIn("не измерены", result.details)

    async def test_missing_characteristic_is_not_written_by_handle(self):
        client = self.client(None)
        result = await request_low_power(client)
        self.assertFalse(result.requested)
        client.write_gatt_char.assert_not_awaited()

    async def test_unacknowledged_only_characteristic_is_not_used(self):
        client = self.client(SimpleNamespace(properties=["write-without-response"]))
        self.assertFalse((await request_low_power(client)).requested)
        client.write_gatt_char.assert_not_awaited()

    async def test_failed_write_is_not_claimed_as_success(self):
        for error in (BleakError("refused"), OSError("offline"), asyncio.TimeoutError()):
            with self.subTest(error=error):
                client = self.client(SimpleNamespace(properties=["write"]))
                client.write_gatt_char.side_effect = error
                self.assertFalse((await request_low_power(client)).requested)


class BackoffTests(unittest.TestCase):
    def test_grows_and_is_capped(self):
        policy = ReconnectBackoff()
        self.assertEqual([policy.next_delay() for _ in range(8)], [15, 30, 60, 120, 240, 300, 300, 300])

    def test_reset(self):
        policy = ReconnectBackoff()
        policy.next_delay()
        policy.next_delay()
        policy.reset()
        self.assertEqual(policy.next_delay(), 15)


if __name__ == "__main__":
    unittest.main()
