"""Desktop widget for a Xiaomi LYWSD03MMC with stock Bluetooth firmware."""

import argparse
import asyncio
from collections import deque
import csv
from dataclasses import dataclass
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import time

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from PySide6.QtCore import QLockFile, QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QDialog, QMenu, QMessageBox, QWidget,
)

from battery_history import BatteryHistory
from power_saving import (
    DEFAULT_READ_INTERVAL, FIRST_MEASUREMENT_TIMEOUT, ReconnectBackoff,
    STABLE_SESSION_SECONDS, request_low_power,
)
from sensor import MEASUREMENTS_UUID, decode_measurement
from settings_dialog import SettingsDialog


LOG = logging.getLogger("mi-temperature-widget")
STALE_SECONDS = 120
DEVICE_DISCOVERY_TIMEOUT = 30
DEFAULT_WIDGET_SIZE = QSize(336, 336)
MINIMUM_WIDGET_SIZE = QSize(224, 224)
MAXIMUM_WIDGET_SIZE = QSize(400, 400)


@dataclass
class SessionResult:
    measurements: int = 0
    duration: float = 0
    scheduled: bool = False


def elapsed_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60:02d}:{seconds % 60:02d}"
    if seconds < 86400:
        return f"{seconds // 3600} ч {(seconds % 3600) // 60:02d} мин"
    return f"{seconds // 86400} д {(seconds % 86400) // 3600} ч"


class BluetoothWorker(QThread):
    measurement = Signal(float, int, float)
    status = Signal(str, str)
    selected = Signal(str)
    diagnostics = Signal(str)
    scheduled = Signal(int)
    device_cached = Signal(object)

    def __init__(self, address="", parent=None, *, mode="continuous", read_interval=DEFAULT_READ_INTERVAL, cached_device=None):
        super().__init__(parent)
        self.address = address
        self.cached_device = cached_device if (
            cached_device is not None and address and cached_device.address.upper() == address.upper()
        ) else None
        self.stop_requested = threading.Event()
        self.loop = None
        self.task = None
        self.mode = mode
        self.read_interval = max(DEFAULT_READ_INTERVAL, read_interval)
        self.backoff = ReconnectBackoff()
        self.connection_attempts = 0
        self.disconnects = deque()
        self.power_details = "Запрос экономии ещё не отправлен."

    def report_diagnostics(self):
        now = time.monotonic()
        while self.disconnects and now - self.disconnects[0] > 900:
            self.disconnects.popleft()
        message = (
            f"Попыток подключения: {self.connection_attempts}. "
            f"Неожиданных обрывов за 15 мин: {len(self.disconnects)}. "
            f"Неудачных циклов подряд: {self.backoff.failures}.\n{self.power_details}"
        )
        if len(self.disconnects) >= 3:
            message += "\nЧастые обрывы могут повышать расход: приблизьте датчик или адаптер. RSSI не доказывает расход тока."
        self.diagnostics.emit(message)
        return len(self.disconnects)

    def stop(self):
        self.stop_requested.set()
        if self.loop is not None and self.task is not None:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass  # The event loop has already finished.

    def run(self):
        try:
            asyncio.run(self.listen())
        except asyncio.CancelledError:
            pass
        except Exception:
            LOG.exception("Bluetooth worker stopped unexpectedly")
            self.status.emit("Ошибка Bluetooth", "Подробности записаны в журнал.")

    async def listen(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        while not self.stop_requested.is_set():
            result = SessionResult()
            error_details = "Проверьте Bluetooth, расстояние и подключение Mi Home."
            try:
                result = await self.connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOG.warning("Bluetooth connection failed: %s", error, exc_info=True)
                error_details = str(error)
            if self.stop_requested.is_set():
                break
            if result.scheduled:
                self.backoff.reset()
                delay = self.read_interval
                self.scheduled.emit(delay)
                self.status.emit("Пауза · соединение закрыто", self.power_details)
            else:
                if result.measurements and result.duration >= STABLE_SESSION_SECONDS:
                    self.backoff.reset()
                delay = self.backoff.next_delay()
                frequent = self.report_diagnostics() >= 3 or self.backoff.failures >= 3
                label = "Проверьте сигнал" if frequent else "Нет связи"
                self.status.emit(f"{label} · повтор через {delay} с", error_details)
            self.report_diagnostics()
            await asyncio.sleep(delay)

    async def connect_once(self):
        device = self.cached_device
        if device is not None and device.address.upper() != self.address.upper():
            self.forget_device()
            device = None
        if device is None:
            self.status.emit("Поиск датчика…", "Поиск до 30 секунд. Держите датчик рядом и закройте Mi Home.")
            if self.address:
                device = await BleakScanner.find_device_by_address(self.address, timeout=DEVICE_DISCOVERY_TIMEOUT)
            else:
                devices = await BleakScanner.discover(timeout=DEVICE_DISCOVERY_TIMEOUT)
                matches = [d for d in devices if "LYWSD03MMC" in (d.name or "").upper()]
                if len(matches) > 1:
                    addresses = "\n".join(f"{d.name}: {d.address}" for d in matches)
                    self.status.emit(
                        "Выберите датчик в настройках",
                        f"Найдено несколько датчиков. Укажите адрес:\n{addresses}",
                    )
                    raise RuntimeError(f"Выберите датчик в настройках. Найденные адреса:\n{addresses}")
                device = matches[0] if matches else None
        if device is None:
            self.status.emit("Датчик не найден", "Нет Bluetooth-рекламы за 30 секунд. Проверьте расстояние и Mi Home.")
            raise BleakError("Датчик не найден за 30 секунд. Возможно, он подключён к Mi Home или вне зоны связи.")

        try:
            result = await self.read_device(device)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A stale endpoint must not prevent rediscovery after sleep/adapter changes.
            self.forget_device()
            raise
        if not result.measurements:
            self.forget_device()
        return result

    def forget_device(self):
        self.cached_device = None
        self.device_cached.emit(None)

    async def read_device(self, device):
        self.status.emit("Подключение…", device.address)
        LOG.info("Connecting to %s (%s)", device.address, "cached device" if device is self.cached_device else "discovered device")
        self.connection_attempts += 1
        self.report_diagnostics()
        disconnected = asyncio.Event()
        last_packet = time.monotonic()
        packets = 0

        def notify(_characteristic, data):
            nonlocal last_packet, packets
            try:
                temperature, humidity, voltage = decode_measurement(data)
            except ValueError:
                LOG.warning("Unexpected notification: %s", data.hex())
                return
            last_packet = time.monotonic()
            packets += 1
            self.measurement.emit(temperature, humidity, voltage)

        async with BleakClient(
            device, disconnected_callback=lambda _client: disconnected.set(), timeout=30
        ) as client:
            connected_at = time.monotonic()
            if client.services.get_characteristic(MEASUREMENTS_UUID) is None:
                raise RuntimeError(
                    "Прошивка не предоставляет ожидаемую характеристику измерений. "
                    "Этот виджет рассчитан на штатную прошивку LYWSD03MMC."
                )
            self.status.emit("Ожидание показаний…", f"Подключён: {device.address}")
            self.address = device.address
            self.selected.emit(device.address)
            self.cached_device = device
            self.device_cached.emit(device)
            await client.start_notify(MEASUREMENTS_UUID, notify)
            power = await request_low_power(client)
            self.power_details = power.details
            LOG.info("Power request for %s: %s", device.address, power.details)
            self.report_diagnostics()
            # Without the interval request, never leave a high-rate link open indefinitely.
            read_once = self.mode == "periodic" or not power.requested
            if not power.requested:
                self.power_details += f" Чтение по одному измерению каждые {self.read_interval // 60} мин."
            first_deadline = time.monotonic() + FIRST_MEASUREMENT_TIMEOUT
            while not disconnected.is_set():
                if read_once and packets:
                    return SessionResult(packets, time.monotonic() - connected_at, scheduled=True)
                try:
                    await asyncio.wait_for(disconnected.wait(), timeout=1 if read_once else 5)
                except asyncio.TimeoutError:
                    if not packets and time.monotonic() >= first_deadline:
                        self.disconnects.append(time.monotonic())
                        raise TimeoutError("Первое измерение не получено за 30 секунд.")
                    if time.monotonic() - last_packet > STALE_SECONDS:
                        self.disconnects.append(time.monotonic())
                        raise TimeoutError("Датчик не передаёт измерения больше двух минут.")
            # If the last valid packet arrived just before a deliberate one-shot exit,
            # this cycle is still useful; no need to reconnect immediately.
            if read_once and packets:
                return SessionResult(packets, time.monotonic() - connected_at, scheduled=True)
            self.disconnects.append(time.monotonic())
            return SessionResult(packets, time.monotonic() - connected_at)


class SensorWidget(QWidget):
    def __init__(self, demo=False):
        super().__init__()
        self.settings = QSettings("MiTemperatureSensor", "DesktopWidget")
        self.title = self.settings.value("title", "Дом") or "Дом"
        self.address = self.settings.value("address", "")
        self.known_device = None
        self.read_mode = self.settings.value("read_mode", "periodic")
        if self.read_mode not in ("continuous", "periodic"):
            self.read_mode = "periodic"
        self.read_interval = max(300, min(3600, self.settings.value("read_interval", 300, type=int)))
        self.demo = demo
        self.temperature = None
        self.humidity = None
        self.voltage = None
        self.updated_at = None
        self.state = "Поиск датчика…"
        self.details = ""
        self.power_diagnostics = ""
        self.sleep_until = None
        self.freshness_seconds = STALE_SECONDS
        self.battery_history = None
        if not demo:
            try:
                self.battery_history = BatteryHistory(log_path().parent / "battery.csv")
            except (OSError, csv.Error):
                LOG.exception("Cannot read battery history")
        self.drag_offset = None
        self.resize_edges = Qt.Edge(0)
        self.resize_origin = None
        self.resize_geometry = None
        self.worker = None
        self.closing = False
        self.restart_pending = False

        self.setWindowTitle("Датчик · " + self.title)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.setInterval(250)
        self.geometry_save_timer.timeout.connect(self.save_geometry)
        self.setMinimumSize(MINIMUM_WIDGET_SIZE)
        self.setMaximumSize(MAXIMUM_WIDGET_SIZE)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.apply_window_flags()
        self.restore_size()
        self.restore_position()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        if demo:
            self.receive_measurement(24.8, 48, 2.95)
            self.updated_at -= 3
            self.state = "Демонстрация · без Bluetooth"
        else:
            self.start_worker()

    def apply_window_flags(self):
        # Ignore legacy on_top settings: the always-on-top feature was removed.
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)

    def restore_size(self):
        saved = self.settings.value("size", DEFAULT_WIDGET_SIZE)
        if not isinstance(saved, QSize) or not saved.isValid():
            saved = DEFAULT_WIDGET_SIZE
        position = self.settings.value("position")
        screen = QApplication.primaryScreen()
        if isinstance(position, QPoint):
            screen = next(
                (s for s in QApplication.screens() if s.availableGeometry().contains(position + QPoint(100, 100))),
                screen,
            )
        side = min(self.square_size(saved).width(), screen.availableGeometry().width(), screen.availableGeometry().height())
        self.resize(side, side)

    @staticmethod
    def square_size(size):
        side = max(MINIMUM_WIDGET_SIZE.width(), min(max(size.width(), size.height()), MAXIMUM_WIDGET_SIZE.width()))
        return QSize(side, side)

    def save_geometry(self):
        self.settings.setValue("position", self.pos())
        self.settings.setValue("size", self.square_size(self.size()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.size().height():
            self.resize(self.square_size(event.size()))
        if hasattr(self, "geometry_save_timer"):
            self.geometry_save_timer.start()

    def layout_scale(self):
        return min(self.width() / DEFAULT_WIDGET_SIZE.width(), self.height() / DEFAULT_WIDGET_SIZE.height())

    def restore_position(self):
        saved = self.settings.value("position")
        screen = next(
            (s for s in QApplication.screens() if isinstance(saved, QPoint)
             and s.availableGeometry().contains(saved + QPoint(100, 100))),
            None,
        )
        if screen is not None:
            area = screen.availableGeometry()
            self.move(
                max(area.left(), min(saved.x(), area.right() - self.width() + 1)),
                max(area.top(), min(saved.y(), area.bottom() - self.height() + 1)),
            )
        else:
            area = QApplication.primaryScreen().availableGeometry()
            self.move(
                max(area.left(), area.right() - self.width() - 24),
                max(area.top(), min(area.top() + 40, area.bottom() - self.height() + 1)),
            )

    def start_worker(self):
        self.worker = BluetoothWorker(
            self.address, self, mode=self.read_mode, read_interval=self.read_interval, cached_device=self.known_device,
        )
        self.worker.measurement.connect(self.receive_measurement)
        self.worker.status.connect(self.receive_status)
        self.worker.selected.connect(self.remember_address)
        self.worker.device_cached.connect(self.remember_device)
        self.worker.diagnostics.connect(self.receive_diagnostics)
        self.worker.scheduled.connect(self.receive_schedule)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def worker_finished(self):
        old_worker = self.worker
        self.worker = None
        old_worker.deleteLater()
        if self.closing:
            self.close()
        elif self.restart_pending:
            self.restart_pending = False
            self.start_worker()

    def remember_address(self, address):
        if self.restart_pending or self.closing:
            return
        self.address = address
        self.settings.setValue("address", address)

    def remember_device(self, device):
        if self.restart_pending or self.closing:
            return
        if device is None or device.address.upper() == self.address.upper():
            self.known_device = device

    def receive_status(self, state, details):
        if self.restart_pending or self.closing:
            return
        self.state = state
        self.details = details
        if state != "Пауза · соединение закрыто":
            self.sleep_until = None
        self.tick()

    def receive_diagnostics(self, details):
        if self.restart_pending or self.closing:
            return
        self.power_diagnostics = details
        self.tick()

    def receive_schedule(self, seconds):
        if self.restart_pending or self.closing:
            return
        self.sleep_until = time.monotonic() + seconds
        self.freshness_seconds = max(STALE_SECONDS, seconds + 45)
        self.tick()

    def receive_measurement(self, temperature, humidity, voltage):
        if self.restart_pending or self.closing:
            return
        self.temperature = temperature
        self.humidity = humidity
        self.voltage = voltage
        self.updated_at = time.monotonic()
        self.state = "Подключён"
        self.sleep_until = None
        self.freshness_seconds = self.read_interval + 45 if self.read_mode == "periodic" else STALE_SECONDS
        if self.battery_history is not None:
            try:
                self.battery_history.record(self.address, voltage)
            except (OSError, csv.Error):
                LOG.exception("Cannot save battery history")
        self.tick()

    def tick(self):
        self.setToolTip(self.compact_tooltip())
        self.update()

    def voltage_text(self):
        return "" if self.voltage is None else f"{self.voltage:.3f}".replace(".", ",") + " В"

    def compact_tooltip(self):
        state = self.card_status()
        if not state:
            state = f"Обновление раз в {self.read_interval // 60} мин" if self.sleep_until is not None else "Подключён"
        lines = [state]
        if self.voltage is not None:
            lines.append(f"Батарейка: {self.voltage_text()}")
        if self.updated_at is not None:
            lines.append(f"Измерение: {elapsed_text(time.monotonic() - self.updated_at)} назад")
        else:
            lines.append("Измерений пока нет")
        return "\n".join(lines)

    def card_status(self):
        """Only actionable status is visible; scheduled waits never show a timer."""
        if self.demo:
            return "Демонстрация · без Bluetooth"
        if self.updated_at is not None and time.monotonic() - self.updated_at >= self.freshness_seconds:
            return "Данные устарели"
        if self.sleep_until is not None or self.state == "Подключён":
            return ""
        return self.state.split(" · повтор через", 1)[0]

    def readable_font_size(self, nominal, minimum):
        """Canvas font size with a readable lower bound after widget scaling."""
        return max(nominal, math.ceil(minimum / self.layout_scale()))

    @staticmethod
    def card_sections(header_height, temperature_height, footer_height=0):
        """Equal gaps between the three blocks, measured using actual font metrics."""
        gap = max(0, (276 - header_height - temperature_height - 54 - footer_height) / 2)
        header = QRectF(30, 30, 276, header_height)
        temperature = QRectF(30, header.bottom() + gap, 276, temperature_height)
        humidity = QRectF(30, temperature.bottom() + gap, 276, 54)
        return header, temperature, humidity

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self.layout_scale()
        margin = 9 * scale
        surface = QLinearGradient(0, 0, self.width(), self.height())
        surface.setColorAt(0, QColor("#292a2d"))
        surface.setColorAt(1, QColor("#1d1e20"))
        painter.setPen(QPen(QColor(255, 255, 255, 30), max(0.8, scale)))
        painter.setBrush(surface)
        painter.drawRoundedRect(
            QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin),
            20 * scale, 20 * scale,
        )
        # Scale text/icons uniformly, even if the card's aspect ratio changes.
        painter.translate(
            (self.width() - DEFAULT_WIDGET_SIZE.width() * scale) / 2,
            (self.height() - DEFAULT_WIDGET_SIZE.height() * scale) / 2,
        )
        painter.scale(scale, scale)

        age = time.monotonic() - self.updated_at if self.updated_at is not None else None
        stale = age is None or age >= self.freshness_seconds
        connected = not stale and (self.state == "Подключён" or self.sleep_until is not None)
        primary = QColor("#f5f5f5")
        muted = QColor("#a2a6ad")
        accent = QColor("#60cdff")
        readings = muted if stale else primary

        def text(rect, value, size, color, weight=QFont.Weight.Normal, align=Qt.AlignmentFlag.AlignLeft):
            painter.setPen(color)
            font = QFont("Segoe UI")
            font.setPixelSize(size)
            font.setWeight(weight)
            painter.setFont(font)
            value = painter.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, int(rect.width()))
            painter.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, value)

        def font(size, weight=QFont.Weight.Normal):
            result = QFont("Segoe UI")
            result.setPixelSize(size)
            result.setWeight(weight)
            return result

        title_size = self.readable_font_size(20, 16)
        subtitle_size = self.readable_font_size(12, 11)
        caption_size = self.readable_font_size(13, 11)
        humidity_size = self.readable_font_size(14, 12)
        humidity_value_size = self.readable_font_size(23, 18)
        unit_size = self.readable_font_size(25, 18)
        status_size = self.readable_font_size(11, 10)
        title_height = QFontMetricsF(font(title_size, QFont.Weight.DemiBold)).height()
        subtitle_height = QFontMetricsF(font(subtitle_size)).height()
        caption_height = QFontMetricsF(font(caption_size)).height()
        header_height = max(42, title_height + subtitle_height + 2)
        value = "—" if self.temperature is None else f"{self.temperature:.1f}".replace(".", ",")
        number_font = font(self.readable_font_size(76, 54), QFont.Weight.Light)
        unit_width = QFontMetricsF(font(unit_size)).horizontalAdvance("°C") + 4
        available_width = 276 - unit_width - 10
        width = QFontMetricsF(number_font).horizontalAdvance(value)
        if width > available_width:
            number_font.setPixelSize(max(40, int(number_font.pixelSize() * available_width / width)))
            width = QFontMetricsF(number_font).horizontalAdvance(value)
        number_ink = QFontMetricsF(number_font).tightBoundingRect(value)
        temperature_height = number_ink.height() + 6 + caption_height
        status = self.card_status()
        battery = self.voltage_text()
        footer_height = QFontMetricsF(font(status_size)).height() + 6 if status or battery else 0
        header, temperature, humidity_row = self.card_sections(header_height, temperature_height, footer_height)
        badge_top = header.center().y() - 21
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(96, 205, 255, 20))
        painter.drawRoundedRect(QRectF(30, badge_top, 42, 42), 12, 12)
        painter.save()
        painter.translate(0, badge_top - 30)
        self.draw_house(painter, accent)
        painter.restore()
        text(QRectF(84, header.top(), 191, title_height), self.title, title_size, primary, QFont.Weight.DemiBold)
        text(QRectF(84, header.top() + title_height + 2, 191, subtitle_height), "Датчик климата", subtitle_size, muted)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#6ccb5f") if connected else QColor("#e6b85c"))
        painter.drawEllipse(QRectF(291, header.top() + title_height / 2 - 3.5, 7, 7))

        # Draw against the glyph's ink bounds, not a tall line box with hidden leading.
        painter.setPen(readings)
        painter.setFont(number_font)
        painter.drawText(QPointF(temperature.left() - number_ink.left(), temperature.top() - number_ink.top()), value)
        text(
            QRectF(40 + width, temperature.top() + number_ink.height() * 0.12, unit_width, QFontMetricsF(font(unit_size)).height()),
            "°C", unit_size, readings,
        )
        text(QRectF(30, temperature.bottom() - caption_height, 276, caption_height), "Температура", caption_size, muted)

        painter.setPen(QPen(QColor(255, 255, 255, 13), 1))
        painter.setBrush(QColor(255, 255, 255, 7))
        painter.drawRoundedRect(humidity_row, 12, 12)
        painter.save()
        painter.translate(0, humidity_row.center().y() - 267)
        self.draw_humidity(painter, accent if not stale else muted)
        painter.restore()
        humidity = "— %" if self.humidity is None else f"{self.humidity}%"
        humidity_width = min(96, max(64, QFontMetricsF(font(humidity_value_size, QFont.Weight.DemiBold)).horizontalAdvance(humidity) + 4))
        humidity_left = humidity_row.right() - 20 - humidity_width
        text(QRectF(76, humidity_row.top() + 9, humidity_left - 88, 36), "Влажность", humidity_size, muted)
        text(QRectF(humidity_left, humidity_row.top() + 9, humidity_width, 36), humidity, humidity_value_size, readings,
             QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignRight)
        if footer_height:
            footer = QRectF(30, 306 - footer_height + 6, 276, footer_height - 6)
            battery_width = 0
            if battery:
                battery_color = QColor("#737982") if stale else QColor("#8b909a")
                value_width = QFontMetricsF(font(status_size)).horizontalAdvance(battery) + 4
                battery_width = value_width + 22
                value_rect = QRectF(footer.right() - value_width, footer.top(), value_width, footer.height())
                icon = QRectF(value_rect.left() - 22, footer.center().y() - 4.5, 15, 9)
                painter.setPen(QPen(battery_color, max(1, 1 / scale)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(icon, 1.5, 1.5)
                painter.drawLine(QPointF(icon.right() + 2, icon.top() + 3), QPointF(icon.right() + 2, icon.bottom() - 3))
                text(value_rect, battery, status_size, battery_color, align=Qt.AlignmentFlag.AlignRight)
            if status:
                label = "Демонстрация" if self.demo else status
                text(QRectF(footer.left(), footer.top(), max(0, footer.width() - battery_width - 8), footer.height()),
                     label, status_size, QColor("#e6b85c"))

    @staticmethod
    def draw_house(painter, color):
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        roof = QPainterPath()
        roof.moveTo(39, 49)
        roof.lineTo(51, 38)
        roof.lineTo(63, 49)
        painter.drawPath(roof)
        house = QPainterPath()
        house.moveTo(42, 49)
        house.lineTo(42, 62)
        house.lineTo(48, 62)
        house.lineTo(48, 54)
        house.lineTo(54, 54)
        house.lineTo(54, 62)
        house.lineTo(60, 62)
        house.lineTo(60, 49)
        painter.drawPath(house)

    @staticmethod
    def draw_humidity(painter, color):
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        drop = QPainterPath()
        drop.moveTo(52, 255)
        drop.cubicTo(49, 260, 45, 263, 45, 267)
        drop.cubicTo(45, 277, 59, 277, 59, 267)
        drop.cubicTo(59, 263, 55, 260, 52, 255)
        drop.closeSubpath()
        painter.drawPath(drop)
        shine = QPainterPath()
        shine.moveTo(49, 267)
        shine.quadTo(49, 271, 52, 271)
        painter.drawPath(shine)

    def resize_edges_at(self, point):
        if not self.rect().contains(point):
            return Qt.Edge(0)
        margin = max(18, 18 * self.layout_scale())
        corner = max(30, 30 * self.layout_scale())
        x, y = point.x(), point.y()
        left, right = x < margin, x >= self.width() - margin
        top, bottom = y < margin, y >= self.height() - margin
        # The rounded corners sit inward from the rectangular window corners.
        if x < corner and y < corner:
            left = top = True
        elif x >= self.width() - corner and y < corner:
            right = top = True
        elif x < corner and y >= self.height() - corner:
            left = bottom = True
        elif x >= self.width() - corner and y >= self.height() - corner:
            right = bottom = True
        edges = Qt.Edge(0)
        for enabled, edge in (
            (left, Qt.Edge.LeftEdge), (right, Qt.Edge.RightEdge),
            (top, Qt.Edge.TopEdge), (bottom, Qt.Edge.BottomEdge),
        ):
            if enabled:
                edges |= edge
        return edges

    @staticmethod
    def cursor_for_edges(edges):
        if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeBDiagCursor
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    @staticmethod
    def resized_geometry(original, delta, edges, minimum, maximum=MAXIMUM_WIDGET_SIZE):
        if not edges:
            return QRect(original)
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        width_change = -delta.x() if edges & Qt.Edge.LeftEdge else delta.x()
        height_change = -delta.y() if edges & Qt.Edge.TopEdge else delta.y()
        if horizontal and vertical:
            # Follow the dominant drag axis, keeping the opposite corner fixed.
            change = width_change if abs(width_change) >= abs(height_change) else height_change
        else:
            change = width_change if horizontal else height_change
        side = max(
            max(minimum.width(), minimum.height()),
            min(original.width() + change, min(maximum.width(), maximum.height())),
        )
        if edges & Qt.Edge.LeftEdge:
            left = original.x() + original.width() - side
        elif edges & Qt.Edge.RightEdge:
            left = original.x()
        else:
            left = original.x() + (original.width() - side) // 2
        if edges & Qt.Edge.TopEdge:
            top = original.y() + original.height() - side
        elif edges & Qt.Edge.BottomEdge:
            top = original.y()
        else:
            top = original.y() + (original.height() - side) // 2
        return QRect(left, top, side, side)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self.resize_edges_at(event.position().toPoint())
            if edges:
                self.drag_offset = None
                self.resize_edges = edges
                self.resize_origin = event.globalPosition().toPoint()
                self.resize_geometry = self.geometry()
                self.setCursor(self.cursor_for_edges(edges))
            else:
                self.drag_offset = event.globalPosition().toPoint() - self.pos()
                self.unsetCursor()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.resize_origin
            self.setGeometry(self.resized_geometry(
                self.resize_geometry, delta, self.resize_edges, self.minimumSize(), self.maximumSize(),
            ))
            event.accept()
        elif self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()
        else:
            self.setCursor(self.cursor_for_edges(self.resize_edges_at(event.position().toPoint())))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = None
            self.resize_edges = Qt.Edge(0)
            self.resize_origin = self.resize_geometry = None
            self.save_geometry()
            self.setCursor(self.cursor_for_edges(self.resize_edges_at(event.position().toPoint())))
            event.accept()

    def leaveEvent(self, event):
        if not self.resize_edges and self.drag_offset is None:
            self.unsetCursor()
        super().leaveEvent(event)

    def create_context_menu(self):
        menu = QMenu(self)
        menu.addAction("Настройки…", self.show_settings)
        if not self.demo:
            menu.addAction("Переподключиться", self.reconnect)
        menu.addSeparator()
        menu.addAction("Закрыть", self.close)
        return menu

    def contextMenuEvent(self, event):
        menu = self.create_context_menu()
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()

    def reconnect(self):
        if self.demo:
            return
        self.state = "Переподключение…"
        self.sleep_until = None
        self.update()
        if self.worker is not None:
            self.restart_pending = True
            self.worker.stop()
        else:
            self.start_worker()

    def show_settings(self):
        dialog = SettingsDialog(
            self.title, self.address, self.read_mode, self.read_interval, self,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            title = dialog.title_edit.text().strip() or "Дом"
            address = dialog.address_edit.text().strip()
            mode = dialog.mode_combo.currentData()
            interval = dialog.interval_spin.value() * 60
        finally:
            dialog.deleteLater()
        changed_address = address != self.address
        changed_mode = mode != self.read_mode or interval != self.read_interval
        self.title = title
        self.address = address
        self.read_mode = mode
        self.read_interval = interval
        self.settings.setValue("title", self.title)
        self.settings.setValue("address", self.address)
        self.settings.setValue("read_mode", self.read_mode)
        self.settings.setValue("read_interval", self.read_interval)
        self.setWindowTitle("Датчик · " + self.title)
        if changed_address or changed_mode:
            if changed_address:
                self.known_device = None
                self.temperature = self.humidity = self.voltage = self.updated_at = None
            self.reconnect()
        self.update()

    def closeEvent(self, event):
        self.geometry_save_timer.stop()
        self.save_geometry()
        self.settings.sync()
        self.closing = True
        self.restart_pending = False
        if self.worker is not None:
            event.ignore()
            self.hide()
            self.worker.stop()
        else:
            event.accept()
            QApplication.instance().quit()


def log_path():
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MiTemperatureSensor"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "widget.log"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Show a preview without Bluetooth")
    args = parser.parse_args()
    logging.basicConfig(
        handlers=[RotatingFileHandler(log_path(), maxBytes=512_000, backupCount=2, encoding="utf-8")],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    # Two copies competing for the same sensor can cause needless reconnects.
    instance_lock = QLockFile(str(log_path().parent / "widget.lock"))
    instance_lock.setStaleLockTime(0)  # Long-lived window; age alone must not invalidate its lock.
    if not args.demo and not instance_lock.tryLock(0):
        QMessageBox.information(None, "Датчик Xiaomi", "Виджет уже запущен. Второе подключение не создаётся.")
        return 1
    app.setStyle("Fusion")
    app.setStyleSheet(
        "QDialog, QMenu { background: #202124; color: #f5f5f5; font-family: 'Segoe UI'; }"
        "QLabel { color: #f5f5f5; }"
        "QLineEdit, QComboBox, QSpinBox { background: #2c2d30; color: #f5f5f5; padding: 8px; "
        "border: 1px solid #45474c; border-radius: 6px; }"
        "QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #60cdff; }"
        "QComboBox QAbstractItemView { background: #2c2d30; color: #f5f5f5; "
        "selection-background-color: #394650; }"
        "QPushButton { background: #323438; color: #f5f5f5; padding: 8px 16px; "
        "border: 1px solid #45474c; border-radius: 6px; }"
        "QPushButton:hover { background: #3a3d42; } QPushButton:default { "
        "background: #60cdff; color: #10222c; border-color: #60cdff; }"
        "QMenu { padding: 5px; border: 1px solid #45474c; border-radius: 8px; }"
        "QMenu::item { padding: 8px 20px; border-radius: 4px; } "
        "QMenu::item:selected { background: #34383d; }"
        "QToolTip { background: #2c2d30; color: #d4d7dc; font-family: 'Segoe UI'; "
        "font-size: 12px; padding: 6px 8px; border: 1px solid #45474c; }"
    )
    widget = SensorWidget(demo=args.demo)
    widget.show()
    result = app.exec()
    instance_lock.unlock()
    return result


if __name__ == "__main__":
    sys.exit(main())
