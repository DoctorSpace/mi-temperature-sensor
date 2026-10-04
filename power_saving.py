"""Stock-firmware power request and a conservative reconnection policy.

Protocol sources:
https://github.com/JsBergbau/MiTemperature2/issues/18#issuecomment-590986874
https://github.com/JsBergbau/MiTemperature2/issues/18#issuecomment-591125109
https://github.com/JsBergbau/MiTemperature2/issues/32
"""

import asyncio
from dataclasses import dataclass

from bleak.exc import BleakError


CONNECTION_INTERVAL_UUID = "ebe0ccd8-7a0a-4b0c-8a1a-6ff2997da3a6"
LOW_POWER_REQUEST = bytes.fromhex("f40100")
DEFAULT_READ_INTERVAL = 300
FIRST_MEASUREMENT_TIMEOUT = 30
STABLE_SESSION_SECONDS = 120


@dataclass(frozen=True)
class PowerSavingResult:
    requested: bool
    details: str


async def request_low_power(client):
    """Request the known stock interval; an ATT ACK is NOT an interval readback.

    Resolve services/characteristics and subscribe before this final GATT write.
    Do not use the legacy 0x0046 handle, which can vary with firmware.
    """
    characteristic = client.services.get_characteristic(CONNECTION_INTERVAL_UUID)
    if characteristic is None or "write" not in characteristic.properties:
        return PowerSavingResult(False, "Команда экономии не поддерживается этой прошивкой.")
    try:
        await asyncio.wait_for(
            client.write_gatt_char(characteristic, LOW_POWER_REQUEST, response=True),
            timeout=10,
        )
    except (BleakError, OSError, asyncio.TimeoutError) as error:
        return PowerSavingResult(False, f"Не удалось отправить команду экономии: {error}")
    return PowerSavingResult(
        True,
        "Запрос экономии f40100 подтверждён GATT. Фактический интервал и ток "
        "не измерены: принятие параметров зависит от Windows и Bluetooth-адаптера.",
    )


class ReconnectBackoff:
    """Do not reset the cooldown merely because one packet got through."""

    def __init__(self):
        self.failures = 0

    def reset(self):
        self.failures = 0

    def next_delay(self):
        self.failures = min(self.failures + 1, 6)
        return min(15 * 2 ** (self.failures - 1), 300)
