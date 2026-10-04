import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QListView, QStyle

from settings_dialog import SettingsDialog
from widget import SensorWidget


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.dialog = SettingsDialog("Дом", "A4:C1:38:63:E8:2F", "periodic", 300)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()

    def test_settings_has_a_gear_window_icon(self):
        self.assertEqual(self.dialog.windowTitle(), "Настройки датчика")
        self.assertFalse(self.dialog.windowIcon().isNull())
        self.assertFalse(self.dialog.windowIcon().pixmap(16, 16).isNull())

    def test_explanatory_and_diagnostic_blocks_are_removed(self):
        labels = [label.text() for label in self.dialog.findChildren(QLabel) if label.text()]
        self.assertEqual(labels, [
            "Настройки", "LYWSD03MMC", "Название виджета", "Bluetooth-адрес",
            "Режим чтения", "Интервал обновления",
        ])

    def test_controls_use_the_full_dialog_width(self):
        self.dialog.show()
        self.app.processEvents()
        for control in (
            self.dialog.title_edit, self.dialog.address_edit,
            self.dialog.mode_combo, self.dialog.interval_spin,
        ):
            with self.subTest(control=control.objectName()):
                self.assertEqual(control.width(), self.dialog.width() - 32)
                self.assertEqual(control.height(), 32)
        self.assertFalse(self.dialog.grab().isNull())
        self.assertEqual(self.dialog.findChild(QLabel, "settingsHeading").font().pixelSize(), 18)

    def test_settings_size_is_compact(self):
        self.dialog.show()
        self.app.processEvents()
        self.assertEqual(self.dialog.size(), QSize(300, 400))
        self.assertTrue(self.dialog.rect().contains(self.dialog.buttons.geometry()))

    def test_form_gaps_are_consistent(self):
        self.dialog.show()
        self.app.processEvents()
        labels = self.dialog.findChildren(QLabel, "fieldLabel")
        controls = [self.dialog.title_edit, self.dialog.address_edit, self.dialog.mode_combo, self.dialog.interval_spin]
        for index, (label, control) in enumerate(zip(labels, controls)):
            self.assertEqual(control.y() - label.geometry().bottom() - 1, 4)
            if index:
                self.assertEqual(label.y() - controls[index - 1].geometry().bottom() - 1, 12)

    def test_control_chevrons_are_available(self):
        assets = Path(__file__).resolve().parents[1] / "assets"
        for name in ("chevron-up.svg", "chevron-down.svg"):
            with self.subTest(name=name):
                self.assertFalse(QPixmap(str(assets / name)).isNull())

    def test_default_values_and_five_minute_interval_are_preserved(self):
        self.assertEqual(self.dialog.title_edit.text(), "Дом")
        self.assertEqual(self.dialog.address_edit.text(), "A4:C1:38:63:E8:2F")
        self.assertEqual(self.dialog.mode_combo.currentData(), "periodic")
        self.assertEqual(self.dialog.interval_spin.value(), 5)

    def test_mode_popup_is_a_compact_list_without_scroll_arrows(self):
        self.dialog.show()
        self.app.processEvents()
        self.assertIsInstance(self.dialog.mode_combo.view(), QListView)
        self.assertEqual(self.dialog.mode_combo.maxVisibleItems(), 2)
        self.assertEqual(self.dialog.mode_combo.view().verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(self.dialog.mode_combo.style().styleHint(QStyle.StyleHint.SH_ComboBox_Popup), 0)
        self.dialog.mode_combo.showPopup()
        self.app.processEvents()
        view = self.dialog.mode_combo.view()
        for row in range(2):
            rect = view.visualRect(view.model().index(row, 0))
            self.assertGreaterEqual(rect.height(), 28)
            self.assertTrue(view.viewport().rect().contains(rect))
        self.dialog.mode_combo.hidePopup()

    def test_interval_is_disabled_for_continuous_reading(self):
        self.dialog.mode_combo.setCurrentIndex(self.dialog.mode_combo.findData("continuous"))
        self.assertFalse(self.dialog.interval_spin.isEnabled())
        self.assertFalse(self.dialog.interval_label.isEnabled())
        self.dialog.mode_combo.setCurrentIndex(self.dialog.mode_combo.findData("periodic"))
        self.assertTrue(self.dialog.interval_spin.isEnabled())

    def test_buttons_are_localized_and_save_is_default(self):
        save = self.dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel = self.dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.assertEqual(save.text(), "Сохранить")
        self.assertEqual(cancel.text(), "Отмена")
        self.assertTrue(save.isDefault())
        save.click()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)


class SettingsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.settings = QSettings("MiTemperatureSensorTests", "SettingsDialog")
        self.settings.clear()
        with patch("widget.QSettings", return_value=self.settings):
            self.widget = SensorWidget(demo=True)

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.settings.clear()

    def test_save_applies_settings_and_reconnects_when_needed(self):
        def accept(dialog):
            dialog.title_edit.setText("Гостиная")
            dialog.address_edit.setText("A4:C1:38:63:E8:2F")
            dialog.interval_spin.setValue(10)
            return QDialog.DialogCode.Accepted

        with patch.object(SettingsDialog, "exec", accept), patch.object(self.widget, "reconnect") as reconnect:
            self.widget.show_settings()
        self.assertEqual(self.widget.title, "Гостиная")
        self.assertEqual(self.settings.value("address"), "A4:C1:38:63:E8:2F")
        self.assertEqual(self.settings.value("read_interval", type=int), 600)
        reconnect.assert_called_once()

    def test_cancel_does_not_change_settings_or_reconnect(self):
        def reject(dialog):
            dialog.title_edit.setText("Изменено")
            dialog.interval_spin.setValue(60)
            return QDialog.DialogCode.Rejected

        with patch.object(SettingsDialog, "exec", reject), patch.object(self.widget, "reconnect") as reconnect:
            self.widget.show_settings()
        self.assertEqual(self.widget.title, "Дом")
        self.assertEqual(self.widget.read_interval, 300)
        self.assertFalse(self.settings.contains("title"))
        reconnect.assert_not_called()

    def test_saving_unchanged_values_does_not_reconnect(self):
        with (
            patch.object(SettingsDialog, "exec", return_value=QDialog.DialogCode.Accepted),
            patch.object(self.widget, "reconnect") as reconnect,
        ):
            self.widget.show_settings()
        self.assertEqual(self.widget.read_interval, 300)
        reconnect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
