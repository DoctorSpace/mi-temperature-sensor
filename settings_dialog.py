"""Compact Fluent-style sensor settings, without protocol diagnostics."""

import math
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListView, QSpinBox, QStyledItemDelegate, QVBoxLayout,
)


def settings_icon():
    """Vector gear: no dependency on an icon font or an external image."""
    icon = QIcon()
    for size in (16, 24, 28, 32, 40, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 32, size / 32)
        gear = QPainterPath()
        for index in range(32):
            angle = index * math.pi / 16 - math.pi / 8
            radius = 12.5 if index % 4 in (1, 2) else 9.5
            x, y = 16 + math.cos(angle) * radius, 16 + math.sin(angle) * radius
            if index == 0:
                gear.moveTo(x, y)
            else:
                gear.lineTo(x, y)
        gear.closeSubpath()
        gear.addEllipse(QRectF(12, 12, 8, 8))
        gear.setFillRule(Qt.FillRule.OddEvenFill)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#60cdff"))
        painter.drawPath(gear)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class ModeItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        view = self.parent()
        size.setWidth(max(1, view.viewport().width() - 2 * view.spacing()))
        return size


class SettingsDialog(QDialog):
    def __init__(self, title, address, mode, interval_seconds, parent=None):
        super().__init__(parent)
        self.setObjectName("sensorSettings")
        self.setWindowTitle("Настройки датчика")
        icon = settings_icon()
        self.setWindowIcon(icon)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setFixedSize(300, 400)
        assets = Path(__file__).resolve().parent / "assets"
        down_arrow = (assets / "chevron-down.svg").as_posix()
        up_arrow = (assets / "chevron-up.svg").as_posix()
        self.setStyleSheet(
            "QDialog#sensorSettings { background: #202124; color: #f5f5f5; }"
            "QLabel, QLineEdit, QComboBox, QSpinBox, QPushButton { "
            "font-family: 'Segoe UI'; font-size: 13px; }"
            "QLabel { color: #f5f5f5; background: transparent; }"
            "QLabel#settingsHeading { font-size: 18px; font-weight: 600; }"
            "QLabel#settingsModel { font-size: 11px; color: #9fa5ae; }"
            "QLabel#settingsIcon { background: #283a44; border-radius: 12px; }"
            "QLabel#fieldLabel { color: #d4d7dc; }"
            "QLabel:disabled { color: #747982; }"
            "QLineEdit, QComboBox, QSpinBox { background: #2b2d31; color: #f5f5f5; "
            "padding: 0 10px; border: 1px solid #45474d; border-radius: 7px; "
            "selection-background-color: #60cdff; selection-color: #10222c; }"
            "QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #60646c; }"
            "QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #60cdff; }"
            "QSpinBox:disabled { background: #25272b; color: #747982; border-color: #373a40; }"
            "QComboBox { padding-right: 34px; combobox-popup: 0; }"
            "QComboBox::drop-down { width: 30px; border: none; }"
            f'QComboBox::down-arrow {{ image: url("{down_arrow}"); width: 12px; height: 12px; }}'
            "QComboBox QAbstractItemView { background: #26282d; color: #f5f5f5; "
            "outline: none; border: 1px solid #45474d; border-radius: 6px; "
            "selection-background-color: #35424d; selection-color: #f5f5f5; padding: 4px; }"
            "QComboBox QAbstractItemView::item { min-height: 28px; padding: 2px 8px; "
            "border: none; border-radius: 4px; }"
            "QComboBox QAbstractItemView::item:selected { background: #35424d; color: #f5f5f5; }"
            "QComboBox QAbstractItemView::item:hover { background: #34383e; }"
            "QSpinBox { padding-right: 32px; }"
            "QSpinBox::up-button, QSpinBox::down-button { width: 28px; "
            "border-left: 1px solid #45474d; background: #32353a; }"
            "QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; "
            "height: 16px; border-top-right-radius: 7px; }"
            "QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; "
            "height: 16px; border-bottom-right-radius: 7px; }"
            "QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #41464e; }"
            f'QSpinBox::up-arrow {{ image: url("{up_arrow}"); width: 12px; height: 12px; }}'
            f'QSpinBox::down-arrow {{ image: url("{down_arrow}"); width: 12px; height: 12px; }}'
            "QFrame#settingsDivider { background: #373a40; border: none; max-height: 1px; }"
            "QPushButton { min-width: 74px; min-height: 30px; padding: 0 10px; "
            "color: #f5f5f5; background: #32353a; border: 1px solid #484c53; border-radius: 7px; }"
            "QPushButton:hover { background: #3d4148; }"
            "QPushButton:pressed { background: #282b30; }"
            "QPushButton:default { color: #10222c; background: #60cdff; border-color: #60cdff; }"
            "QPushButton:default:hover { background: #81d7ff; }"
            "QPushButton:default:pressed { background: #45b8eb; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(10)
        badge = QLabel()
        badge.setObjectName("settingsIcon")
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icon.pixmap(24, 24))
        header.addWidget(badge)
        heading = QVBoxLayout()
        heading.setSpacing(0)
        name = QLabel("Настройки")
        name.setObjectName("settingsHeading")
        name.setFixedHeight(24)
        model = QLabel("LYWSD03MMC")
        model.setObjectName("settingsModel")
        model.setFixedHeight(14)
        heading.addWidget(name)
        heading.addWidget(model)
        header.addLayout(heading)
        header.addStretch()
        layout.addLayout(header)
        layout.addSpacing(14)

        self.title_edit = QLineEdit(title)
        self.title_edit.setObjectName("sensorTitle")
        self.title_edit.setMaxLength(40)
        self.title_edit.setPlaceholderText("Например, Дом")
        self.address_edit = QLineEdit(address)
        self.address_edit.setObjectName("sensorAddress")
        self.address_edit.setPlaceholderText("Автоматический поиск")
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("readingMode")
        mode_view = QListView(self.mode_combo)
        mode_view.setFrameShape(QFrame.Shape.NoFrame)
        mode_view.setResizeMode(QListView.ResizeMode.Adjust)
        mode_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        mode_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        mode_view.setTextElideMode(Qt.TextElideMode.ElideRight)
        mode_view.setSpacing(2)
        mode_view.setItemDelegate(ModeItemDelegate(mode_view))
        self.mode_combo.setView(mode_view)
        self.mode_combo.setMaxVisibleItems(2)
        self.mode_combo.addItem("Постоянное подключение", "continuous")
        self.mode_combo.addItem("Обновление по интервалу", "periodic")
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
        self.interval_spin = QSpinBox()
        self.interval_spin.setObjectName("readingInterval")
        self.interval_spin.setRange(5, 60)
        self.interval_spin.setSuffix(" мин")
        self.interval_spin.setValue(interval_seconds // 60)

        def field(caption, control):
            label = QLabel(caption)
            label.setObjectName("fieldLabel")
            label.setFixedHeight(18)
            label.setBuddy(control)
            control.setAccessibleName(caption)
            control.setFixedHeight(32)
            layout.addWidget(label)
            layout.addSpacing(4)
            layout.addWidget(control)
            layout.addSpacing(12)
            return label

        field("Название виджета", self.title_edit)
        field("Bluetooth-адрес", self.address_edit)
        field("Режим чтения", self.mode_combo)
        self.interval_label = field("Интервал обновления", self.interval_spin)
        self.mode_combo.currentIndexChanged.connect(self.update_interval_enabled)
        self.update_interval_enabled()

        divider = QFrame()
        divider.setObjectName("settingsDivider")
        divider.setFixedHeight(1)
        layout.addStretch(1)
        layout.addWidget(divider)
        layout.addSpacing(12)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setDefault(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.setFixedHeight(32)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.title_edit.setFocus()

    def update_interval_enabled(self):
        enabled = self.mode_combo.currentData() == "periodic"
        self.interval_spin.setEnabled(enabled)
        self.interval_label.setEnabled(enabled)
