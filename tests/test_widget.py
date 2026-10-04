import asyncio
import os
import struct
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSettings, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from widget import BluetoothWorker, MAXIMUM_WIDGET_SIZE, MINIMUM_WIDGET_SIZE, SensorWidget, elapsed_text
from power_saving import CONNECTION_INTERVAL_UUID, LOW_POWER_REQUEST


class ElapsedTextTests(unittest.TestCase):
    def test_formats(self):
        for seconds, expected in (
            (-1, "00:00"), (3, "00:03"), (75, "01:15"),
            (3600, "1 ч 00 мин"), (90000, "1 д 1 ч"),
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(elapsed_text(seconds), expected)


class BluetoothTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_sensor(self):
        worker = BluetoothWorker()
        statuses = []
        worker.status.connect(lambda state, _details: statuses.append(state))
        with patch("widget.BleakScanner.discover", new=AsyncMock(return_value=[])):
            with self.assertRaisesRegex(Exception, "Датчик не найден"):
                await worker.connect_once()
        self.assertEqual(statuses[-1], "Датчик не найден")

    async def test_multiple_sensors_are_not_chosen_arbitrarily(self):
        worker = BluetoothWorker()
        statuses = []
        worker.status.connect(lambda state, details: statuses.append((state, details)))
        devices = [SimpleNamespace(name="LYWSD03MMC", address=address) for address in ("AA", "BB")]
        with patch("widget.BleakScanner.discover", new=AsyncMock(return_value=devices)):
            with self.assertRaisesRegex(RuntimeError, "BB"):
                await worker.connect_once()
        self.assertEqual(statuses[-1][0], "Выберите датчик в настройках")
        self.assertIn("BB", statuses[-1][1])
        self.assertEqual(worker.address, "")

    async def test_measurements_and_disconnection(self):
        worker = BluetoothWorker("AA:BB:CC:DD:EE:FF")
        readings = []
        selections = []
        worker.measurement.connect(lambda *values: readings.append(values))
        worker.selected.connect(selections.append)
        device = SimpleNamespace(name="LYWSD03MMC", address=worker.address)
        writes = []

        class Client:
            def __init__(self, _device, disconnected_callback, timeout):
                self.callback = disconnected_callback
                self.services = SimpleNamespace(
                    get_characteristic=lambda uuid: SimpleNamespace(uuid=uuid, properties=["write"])
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def start_notify(self, _uuid, callback):
                callback(None, struct.pack("<hBH", 2480, 48, 2950))

            async def write_gatt_char(self, characteristic, payload, response):
                writes.append((characteristic.uuid, payload, response))
                asyncio.get_running_loop().call_soon(self.callback, self)

        with (
            patch("widget.BleakScanner.find_device_by_address", new=AsyncMock(return_value=device)),
            patch("widget.BleakClient", Client),
        ):
            result = await worker.connect_once()
        self.assertEqual(readings, [(24.8, 48, 2.95)])
        self.assertEqual(selections, [device.address])
        self.assertEqual(writes, [(CONNECTION_INTERVAL_UUID, LOW_POWER_REQUEST, True)])
        self.assertFalse(result.scheduled)
        self.assertEqual(result.measurements, 1)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.settings = QSettings("MiTemperatureSensorTests", "DesktopWidget")
        self.settings.clear()
        with patch("widget.QSettings", return_value=self.settings):
            self.widget = SensorWidget(demo=True)

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.settings.clear()

    def test_demo_does_not_start_bluetooth(self):
        self.assertIsNone(self.widget.worker)
        self.assertEqual(self.widget.temperature, 24.8)
        self.assertIn("Демонстрация", self.widget.state)

    def test_default_reading_is_every_five_minutes(self):
        self.assertEqual(self.widget.read_mode, "periodic")
        self.assertEqual(self.widget.read_interval, 300)

    def test_widget_renders_without_hardware(self):
        self.widget.show()
        self.app.processEvents()
        self.assertFalse(self.widget.grab().isNull())

    def test_new_measurement_resets_age(self):
        self.widget.updated_at = time.monotonic() - 200
        self.widget.receive_measurement(-5.25, 80, 2.6)
        self.assertLess(time.monotonic() - self.widget.updated_at, 1)
        self.assertEqual(self.widget.state, "Подключён")
        self.assertIn("2,600 В", self.widget.toolTip())

    def test_hover_tooltip_is_compact_without_protocol_diagnostics(self):
        self.widget.demo = False
        self.widget.details = "Запрос экономии f40100 подтверждён GATT. " * 20
        self.widget.power_diagnostics = "Попыток подключения: 10. Неожиданных обрывов: 2."
        self.widget.receive_measurement(21.3, 53, 2.912)
        lines = self.widget.toolTip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "Подключён")
        self.assertEqual(lines[1], "Батарейка: 2,912 В")
        self.assertTrue(lines[2].startswith("Измерение:"))
        self.assertNotIn("GATT", self.widget.toolTip())
        self.assertNotIn("Попыток", self.widget.toolTip())
        self.assertLess(len(self.widget.toolTip()), 100)

    def test_tooltip_reports_periodic_wait_without_showing_technical_details(self):
        self.widget.demo = False
        self.widget.receive_schedule(300)
        self.widget.receive_status("Пауза · соединение закрыто", "Очень длинная диагностика")
        self.assertEqual(self.widget.toolTip().splitlines()[0], "Обновление раз в 5 мин")
        self.assertEqual(len(self.widget.toolTip().splitlines()), 3)

    def test_voltage_text_has_three_decimals_and_no_fake_percentage(self):
        self.widget.voltage = 2.912
        self.assertEqual(self.widget.voltage_text(), "2,912 В")
        self.widget.voltage = None
        self.assertEqual(self.widget.voltage_text(), "")

    def test_no_measurement_tooltip_is_short_and_does_not_invent_voltage(self):
        self.widget.demo = False
        self.widget.voltage = self.widget.updated_at = None
        self.widget.state = "Поиск датчика…"
        self.widget.tick()
        self.assertEqual(self.widget.toolTip(), "Поиск датчика…\nИзмерений пока нет")

    def test_voltage_and_warning_render_together_at_minimum_size(self):
        self.widget.demo = False
        self.widget.updated_at = time.monotonic() - 1000
        self.widget.resize(MINIMUM_WIDGET_SIZE)
        self.widget.show()
        self.app.processEvents()
        self.assertFalse(self.widget.grab().isNull())
        self.assertEqual(self.widget.card_status(), "Данные устарели")
        self.assertIn("Батарейка:", self.widget.toolTip())

    def test_legacy_topmost_setting_is_ignored(self):
        self.settings.setValue("on_top", True)
        with patch("widget.QSettings", return_value=self.settings):
            other = SensorWidget(demo=True)
        try:
            self.assertFalse(other.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        finally:
            other.close()
            other.deleteLater()

    def test_context_menu_contains_only_requested_actions(self):
        self.widget.demo = False
        menu = self.widget.create_context_menu()
        actions = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(actions, ["Настройки…", "Переподключиться", "Закрыть"])

    def test_hit_testing_all_edges_and_rounded_corners(self):
        left, right, top, bottom = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        for point, expected in (
            (QPoint(9, 150), left), (QPoint(326, 150), right),
            (QPoint(150, 9), top), (QPoint(150, 324), bottom),
            (QPoint(25, 25), left | top), (QPoint(310, 25), right | top),
            (QPoint(25, 308), left | bottom), (QPoint(310, 308), right | bottom),
            (QPoint(150, 150), Qt.Edge(0)), (QPoint(-1, 150), Qt.Edge(0)),
        ):
            with self.subTest(point=point):
                self.assertEqual(self.widget.resize_edges_at(point), expected)

    def test_minimum_size_keeps_opposite_corner_anchored(self):
        original = QRect(100, 100, 336, 336)
        resized = self.widget.resized_geometry(
            original, QPoint(1000, 1000), Qt.Edge.LeftEdge | Qt.Edge.TopEdge, MINIMUM_WIDGET_SIZE,
        )
        self.assertEqual(resized.size(), MINIMUM_WIDGET_SIZE)
        self.assertEqual(resized.bottomRight(), original.bottomRight())

    def test_right_bottom_resize_keeps_origin_anchored(self):
        original = QRect(100, 100, 336, 336)
        resized = self.widget.resized_geometry(
            original, QPoint(50, 40), Qt.Edge.RightEdge | Qt.Edge.BottomEdge, MINIMUM_WIDGET_SIZE,
        )
        self.assertEqual(resized.topLeft(), original.topLeft())
        self.assertEqual(resized.size(), QSize(386, 386))

    @staticmethod
    def mouse_event(kind, local, global_point, *, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
        return QMouseEvent(kind, QPointF(local), QPointF(global_point), button, buttons, Qt.KeyboardModifier.NoModifier)

    def test_dragging_right_edge_resizes_and_saves(self):
        self.widget.show()
        self.app.processEvents()
        self.widget.move(100, 100)
        point = QPoint(326, 150)
        start = self.widget.mapToGlobal(point)
        self.widget.mousePressEvent(self.mouse_event(
            QEvent.Type.MouseButtonPress, point, start,
            button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton,
        ))
        end = start + QPoint(80, 0)
        self.widget.mouseMoveEvent(self.mouse_event(
            QEvent.Type.MouseMove, self.widget.mapFromGlobal(end), end, buttons=Qt.MouseButton.LeftButton,
        ))
        self.widget.mouseReleaseEvent(self.mouse_event(
            QEvent.Type.MouseButtonRelease, self.widget.mapFromGlobal(end), end, button=Qt.MouseButton.LeftButton,
        ))
        self.assertEqual(self.widget.size(), QSize(400, 400))
        self.assertEqual(self.widget.pos(), QPoint(100, 68))
        self.assertEqual(self.settings.value("size"), self.widget.size())

    def test_dragging_body_moves_without_resizing(self):
        self.widget.move(100, 100)
        original_size = self.widget.size()
        point = QPoint(150, 150)
        start = self.widget.mapToGlobal(point)
        self.widget.mousePressEvent(self.mouse_event(
            QEvent.Type.MouseButtonPress, point, start,
            button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton,
        ))
        end = start + QPoint(30, 40)
        self.widget.mouseMoveEvent(self.mouse_event(
            QEvent.Type.MouseMove, point, end, buttons=Qt.MouseButton.LeftButton,
        ))
        self.widget.mouseReleaseEvent(self.mouse_event(
            QEvent.Type.MouseButtonRelease, point, end, button=Qt.MouseButton.LeftButton,
        ))
        self.assertEqual(self.widget.pos(), QPoint(130, 140))
        self.assertEqual(self.widget.size(), original_size)

    def test_saved_size_is_restored(self):
        self.widget.resize(380, 360)
        self.widget.save_geometry()
        with patch("widget.QSettings", return_value=self.settings):
            other = SensorWidget(demo=True)
        try:
            self.assertEqual(other.size(), QSize(380, 380))
        finally:
            other.close()
            other.deleteLater()

    def test_resized_card_renders_at_small_large_and_non_square_sizes(self):
        self.widget.show()
        for size in (MINIMUM_WIDGET_SIZE, QSize(672, 668), QSize(650, 334), QSize(336, 600)):
            with self.subTest(size=size):
                self.widget.resize(size)
                self.app.processEvents()
                image = self.widget.grab()
                self.assertFalse(image.isNull())
                self.assertEqual(image.size(), self.widget.square_size(size))

    def test_small_widget_fonts_have_readable_minimum_sizes(self):
        self.widget.resize(MINIMUM_WIDGET_SIZE)
        for nominal, minimum in ((20, 16), (12, 11), (13, 11), (14, 12), (23, 18), (76, 54)):
            with self.subTest(nominal=nominal):
                effective_size = self.widget.readable_font_size(nominal, minimum) * self.widget.layout_scale()
                self.assertGreaterEqual(effective_size, minimum)
                self.assertLess(effective_size, minimum + 1)

    def test_card_sections_have_equal_gaps(self):
        for header_height, temperature_height, status_height in ((42, 95, 0), (57, 100, 0), (57, 100, 28)):
            with self.subTest(header_height=header_height, status_height=status_height):
                header, temperature, humidity = self.widget.card_sections(header_height, temperature_height, status_height)
                first_gap = temperature.top() - header.bottom()
                second_gap = humidity.top() - temperature.bottom()
                self.assertAlmostEqual(first_gap, second_gap)
                self.assertGreaterEqual(first_gap, 0)
                self.assertLessEqual(humidity.bottom() + status_height, 306)

    def test_maximum_size_is_400_by_400(self):
        self.widget.resize(900, 900)
        self.assertEqual(self.widget.maximumSize(), QSize(400, 400))
        self.assertEqual(self.widget.size(), QSize(400, 400))

    def test_maximum_resize_keeps_opposite_corner_anchored(self):
        original = QRect(100, 100, 336, 336)
        resized = self.widget.resized_geometry(
            original, QPoint(-1000, -1000), Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            MINIMUM_WIDGET_SIZE, MAXIMUM_WIDGET_SIZE,
        )
        self.assertEqual(resized.size(), MAXIMUM_WIDGET_SIZE)
        self.assertEqual(resized.bottomRight(), original.bottomRight())

    def test_every_edge_and_corner_keeps_width_equal_to_height(self):
        original = QRect(100, 100, 336, 336)
        left, right, top, bottom = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        for edges in (left, right, top, bottom, left | top, right | top, left | bottom, right | bottom):
            for delta in (QPoint(30, 10), QPoint(-30, -10), QPoint(5, 45), QPoint(1000, -1000)):
                with self.subTest(edges=edges, delta=delta):
                    resized = self.widget.resized_geometry(original, delta, edges, MINIMUM_WIDGET_SIZE)
                    self.assertEqual(resized.width(), resized.height())
                    self.assertGreaterEqual(resized.width(), 224)
                    self.assertLessEqual(resized.width(), 400)

    def test_side_resize_keeps_orthogonal_center(self):
        original = QRect(100, 100, 336, 336)
        horizontal = self.widget.resized_geometry(original, QPoint(40, 0), Qt.Edge.RightEdge, MINIMUM_WIDGET_SIZE)
        vertical = self.widget.resized_geometry(original, QPoint(0, 40), Qt.Edge.BottomEdge, MINIMUM_WIDGET_SIZE)
        self.assertEqual(horizontal.center().y(), original.center().y())
        self.assertEqual(vertical.center().x(), original.center().x())

    def test_non_square_resize_request_is_normalized_when_visible(self):
        self.widget.show()
        self.app.processEvents()
        self.widget.resize(380, 360)
        self.app.processEvents()
        self.assertEqual(self.widget.size(), QSize(380, 380))

    def test_legacy_rectangle_is_restored_as_a_square(self):
        self.settings.setValue("size", QSize(350, 290))
        with patch("widget.QSettings", return_value=self.settings):
            other = SensorWidget(demo=True)
        try:
            self.assertEqual(other.size(), QSize(350, 350))
        finally:
            other.close()
            other.deleteLater()

    def test_saved_oversized_geometry_is_clamped_on_startup(self):
        self.settings.setValue("size", QSize(900, 900))
        with patch("widget.QSettings", return_value=self.settings):
            other = SensorWidget(demo=True)
        try:
            self.assertEqual(other.size(), MAXIMUM_WIDGET_SIZE)
        finally:
            other.close()
            other.deleteLater()

    def test_scheduled_reading_has_no_visible_countdown(self):
        self.widget.demo = False
        self.widget.receive_schedule(300)
        self.widget.receive_status("Пауза · соединение закрыто", "")
        self.assertEqual(self.widget.card_status(), "")

    def test_stale_measurements_still_show_a_warning(self):
        self.widget.demo = False
        self.widget.updated_at = time.monotonic() - 1000
        self.assertEqual(self.widget.card_status(), "Данные устарели")

    def test_retry_status_hides_the_time_on_the_card(self):
        self.widget.demo = False
        self.widget.receive_status("Нет связи · повтор через 300 с", "")
        self.assertEqual(self.widget.card_status(), "Нет связи")

    def test_resize_hover_cursor(self):
        self.assertEqual(self.widget.cursor_for_edges(Qt.Edge.LeftEdge), Qt.CursorShape.SizeHorCursor)
        self.assertEqual(self.widget.cursor_for_edges(Qt.Edge.TopEdge), Qt.CursorShape.SizeVerCursor)
        self.assertEqual(
            self.widget.cursor_for_edges(Qt.Edge.LeftEdge | Qt.Edge.TopEdge), Qt.CursorShape.SizeFDiagCursor,
        )
        self.assertEqual(
            self.widget.cursor_for_edges(Qt.Edge.RightEdge | Qt.Edge.TopEdge), Qt.CursorShape.SizeBDiagCursor,
        )

    def test_scheduled_wait_sets_a_suitable_freshness_window(self):
        self.widget.receive_schedule(300)
        self.assertEqual(self.widget.freshness_seconds, 345)
        self.assertGreater(self.widget.sleep_until, time.monotonic())
        self.widget.show()
        self.app.processEvents()
        self.assertFalse(self.widget.grab().isNull())

    def test_old_worker_cannot_overwrite_a_new_selection(self):
        self.widget.restart_pending = True
        self.widget.address = "NEW"
        self.widget.remember_address("OLD")
        self.assertEqual(self.widget.address, "NEW")

    def test_cached_device_survives_manual_worker_restart(self):
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        self.widget.address = "AA"
        self.widget.remember_device(device)
        with patch("widget.BluetoothWorker") as worker_class:
            try:
                self.widget.start_worker()
                self.assertIs(worker_class.call_args.kwargs["cached_device"], device)
                self.assertEqual(worker_class.call_args.kwargs["read_interval"], 300)
            finally:
                self.widget.worker = None

    def test_cache_invalidation_and_wrong_address_are_handled(self):
        device = SimpleNamespace(name="LYWSD03MMC", address="AA")
        self.widget.address = "AA"
        self.widget.remember_device(device)
        self.widget.remember_device(SimpleNamespace(address="BB"))
        self.assertIs(self.widget.known_device, device)
        self.widget.remember_device(None)
        self.assertIsNone(self.widget.known_device)


if __name__ == "__main__":
    unittest.main()
