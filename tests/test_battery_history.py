import csv
from pathlib import Path
import tempfile
import unittest

from battery_history import BatteryHistory


class BatteryHistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="battery-test-", dir=Path(__file__).parent)
        self.path = Path(self.folder.name) / "battery.csv"

    def tearDown(self):
        self.folder.cleanup()

    def test_records_hourly_and_keeps_voltage_not_percentage(self):
        history = BatteryHistory(self.path)
        self.assertTrue(history.record("aa", 2.95, now=10000))
        self.assertFalse(history.record("AA", 2.94, now=10006))
        self.assertTrue(history.record("AA", 2.90, now=13600))
        with self.path.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["voltage_v"], "2.950")
        self.assertEqual(rows[0]["address"], "AA")

    def test_restart_does_not_duplicate_recent_snapshot(self):
        BatteryHistory(self.path).record("AA", 2.95, now=10000)
        self.assertFalse(BatteryHistory(self.path).record("AA", 2.94, now=10006))

    def test_different_sensors_have_separate_cooldowns(self):
        history = BatteryHistory(self.path)
        self.assertTrue(history.record("AA", 2.95, now=10000))
        self.assertTrue(history.record("BB", 2.90, now=10000))

    def test_unselected_sensor_is_not_logged(self):
        self.assertFalse(BatteryHistory(self.path).record("", 2.95, now=10000))
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
