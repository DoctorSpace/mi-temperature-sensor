"""Hourly voltage snapshots for observing battery behaviour, not estimating %.

Voltage varies with load and temperature. A single dip is not proof of drain.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
import time


class BatteryHistory:
    FIELDS = ("timestamp_utc", "address", "voltage_v")

    def __init__(self, path):
        self.path = Path(path)
        self.last_recorded = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8", newline="") as file:
                for row in csv.DictReader(file):
                    try:
                        timestamp = datetime.fromisoformat(row["timestamp_utc"]).timestamp()
                        self.last_recorded[row["address"]] = timestamp
                    except (ValueError, KeyError, TypeError):
                        continue

    def record(self, address, voltage, now=None):
        if not address:
            return False
        address = address.upper()
        now = time.time() if now is None else now
        previous = self.last_recorded.get(address)
        if previous is not None and 0 <= now - previous < 3600:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=self.FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow({
                "timestamp_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "address": address,
                "voltage_v": f"{voltage:.3f}",
            })
        self.last_recorded[address] = now
        return True
