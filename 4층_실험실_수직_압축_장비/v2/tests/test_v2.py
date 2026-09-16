import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from hydrogel_app.app import HydrogelTesterApp
from hydrogel_app.config import AppSettings, load_settings, save_settings
from hydrogel_app.protocol import jog_command, start_command, stop_command


class ProtocolTests(unittest.TestCase):
    def test_commands_match_v1_firmware_contract(self):
        self.assertEqual(jog_command("JU", "0.5"), b"JU,0.5\n")
        self.assertEqual(jog_command("JD", "0.5"), b"JD,0.5\n")
        self.assertEqual(start_command("5.0", "0.5"), b"start,5.0,0.5\n")
        self.assertEqual(stop_command(), b"STOP\n")


class ConfigTests(unittest.TestCase):
    def test_json_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            expected = AppSettings("COM7", "COM8", temp_dir, "light")
            save_settings(expected, path)
            self.assertEqual(load_settings(path), expected)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["motor_port"], "COM7")


class UiSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = HydrogelTesterApp(
            self.root,
            config_path=Path(self.temp_dir.name) / "settings.json",
            demo=True,
            show_dialogs=False,
        )
        self.root.update()

    def tearDown(self):
        self.app.close()
        self.temp_dir.cleanup()

    def test_jog_buttons_send_same_press_and_release_signals(self):
        self.app.jog_speed_var.set("0.5")
        self.app.down_button.event_generate("<ButtonPress-1>")
        self.app.down_button.event_generate("<ButtonRelease-1>")
        self.app.up_button.event_generate("<ButtonPress-1>")
        self.app.up_button.event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual(
            self.app.arduino.writes,
            [b"JU,0.5\n", b"STOP\n", b"JD,0.5\n", b"STOP\n"],
        )

    def test_start_and_stop_buttons_send_same_signals(self):
        self.app.save_dir_var.set(self.temp_dir.name)
        self.app.distance_var.set("5.0")
        self.app.speed_var.set("0.5")
        self.app.start_button.invoke()
        self.app.stop_button.invoke()
        self.root.update()
        self.assertEqual(
            self.app.arduino.writes[-2:],
            [b"start,5.0,0.5\n", b"STOP\n"],
        )

    def test_save_defaults_button_writes_json(self):
        self.app.motor_port_var.set("com7")
        self.app.indicator_port_var.set("com8")
        self.app.save_defaults_button.invoke()
        self.root.update()
        settings = load_settings(Path(self.temp_dir.name) / "settings.json")
        self.assertEqual(settings.motor_port, "COM7")
        self.assertEqual(settings.indicator_port, "COM8")
        self.assertEqual(settings.theme, "dark")

    def test_theme_button_switches_and_persists_theme(self):
        self.assertEqual(self.app.theme_name, "dark")
        self.app.theme_button.invoke()
        self.root.update()
        settings = load_settings(Path(self.temp_dir.name) / "settings.json")
        self.assertEqual(self.app.theme_name, "light")
        self.assertEqual(settings.theme, "light")
        self.assertEqual(self.app.theme_button.cget("text"), "어두운 모드")


if __name__ == "__main__":
    unittest.main()
