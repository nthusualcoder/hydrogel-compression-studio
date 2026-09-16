import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path

from hydrogel_app.app import HydrogelStudio
from hydrogel_app.config import AppSettings, load_settings, save_settings
from hydrogel_app.profile import PROFILE
from hydrogel_app.protocol import (
    grams_to_newton,
    jog_command,
    motor_off_command,
    motor_on_command,
    parse_indicator_grams,
    start_command,
    stop_command,
)
from hydrogel_app.serial_io import MemorySerial


class ProtocolTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(jog_command("JF", "0.5"), b"JF,0.5\n")
        self.assertEqual(jog_command("JB", 1), b"JB,1\n")
        self.assertEqual(start_command("5.0", "0.5"), b"start,5,0.5\n")
        self.assertEqual(stop_command(), b"STOP\n")
        self.assertEqual(motor_off_command(), b"MOFF\n")
        self.assertEqual(motor_on_command(), b"MON\n")
        with self.assertRaises(ValueError):
            start_command("0", "1")
        with self.assertRaises(ValueError):
            jog_command("JF", "abc")

    def test_indicator_parsing(self):
        self.assertEqual(parse_indicator_grams("   -12.3"), -12.3)
        self.assertEqual(parse_indicator_grams("0.1"), 0.1)
        self.assertEqual(parse_indicator_grams("+  3.4 g"), 3.4)
        self.assertEqual(parse_indicator_grams("ST,GS,-  0.5"), -0.5)
        self.assertIsNone(parse_indicator_grams("OVERLOAD"))
        self.assertIsNone(parse_indicator_grams("99999"))
        self.assertAlmostEqual(grams_to_newton(1000), 9.80665)


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            defaults = AppSettings(motor_port="COM3", indicator_port="COM4")
            self.assertEqual(load_settings(path, defaults).motor_port, "COM3")
            save_settings(AppSettings("COM7", "COM8", d, "light", "1", "2", "3"), path)
            loaded = load_settings(path, defaults)
            self.assertEqual((loaded.motor_port, loaded.theme, loaded.speed), ("COM7", "light", "3"))


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.motor = MemorySerial("M")
        self.loadcell = MemorySerial("L", timeout=0.01)
        devices = {"MOTORPORT": self.motor, "LOADPORT": self.loadcell}
        self.app = HydrogelStudio(
            self.root,
            PROFILE,
            config_path=Path(self.tmp.name) / "s.json",
            serial_factory=lambda port, baud, timeout=0.1: devices[port],
            show_dialogs=False,
        )
        self.app.motor_port_var.set("motorport")
        self.app.indicator_port_var.set("loadport")
        self.app.save_dir_var.set(self.tmp.name)
        self.app.connect_devices()
        self.app.sample_rate_var.set("0")     # 기존 테스트는 원본(줄마다 기록) 기준
        self.root.update()

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def pump(self, seconds=0.15):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def click_hold(self, button):
        """마우스 누름/놓음 이벤트. 이벤트는 창이 화면에 매핑된 상태에서만 전달된다."""
        self.root.deiconify()
        self.root.update()
        button.event_generate("<ButtonPress-1>", x=2, y=2)
        button.event_generate("<ButtonRelease-1>", x=2, y=2)
        self.root.update()

    def test_sample_rate_averaging(self):
        """기록 속도 10/s: 0.1 s 안에 들어온 값들은 평균 1개로 기록된다."""
        from hydrogel_app.protocol import grams_to_newton
        self.app.sample_rate_var.set("10")
        self.app.distance_var.set("1"); self.app.speed_var.set("1")
        self.app.start_button.invoke()
        for g in ("-10.0", "-20.0", "-30.0"):
            self.loadcell.queue_line(g + "\n")
        self.pump(0.15)                      # 아직 버킷에만 쌓임
        self.assertEqual(len(self.app.force_data), 0)
        self.loadcell.queue_line("-40.0\n")  # 간격 경과 후 도착 → 평균 기록
        self.pump(0.1)
        self.assertEqual(len(self.app.force_data), 1)
        self.assertAlmostEqual(self.app.force_data[-1], grams_to_newton(25.0), places=6)
        self.app.stop_button.invoke()        # 종료 시 남은 버킷은 없음
        self.assertEqual(len(self.app.force_data), 1)

    def test_updater_version_compare(self):
        from hydrogel_app.updater import is_newer, parse_version
        self.assertEqual(parse_version("v3.1.2"), (3, 1, 2))
        self.assertTrue(is_newer("v3.0.1", "3.0.0"))
        self.assertFalse(is_newer("v3.0.0", "3.0.0"))
        self.assertTrue(is_newer("v4.0.0", "3.9.9"))

    def test_jog_uses_forward_backward_commands(self):
        self.app.jog_speed_var.set("0.5")
        self.click_hold(self.app.press_button)
        self.click_hold(self.app.release_button)
        self.assertEqual(self.motor.writes, [b"JF,0.5\n", b"STOP\n", b"JB,0.5\n", b"STOP\n"])

    def test_compression_ends_on_done_and_force_sign(self):
        self.app.distance_var.set("2")
        self.app.speed_var.set("1")
        self.app.start_button.invoke()
        self.assertEqual(self.motor.writes[-1], b"start,2,1\n")
        self.loadcell.queue_line("   -10.0\r\n")     # 압축 → 음수 g → +N
        self.pump()
        self.assertAlmostEqual(self.app.force_data[-1], grams_to_newton(10.0), places=6)
        self.motor.queue_line("DONE\n")
        self.pump()
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.app._state, "DONE")
        csv_files = list(Path(self.tmp.name).glob("*.csv"))
        self.assertEqual(len(csv_files), 1)
        text = csv_files[0].read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(text[0], "Time (s),Distance (mm),Force (N)")
        self.assertEqual(len(text), 2)

    def test_poro_keeps_recording_after_done(self):
        self.app.mode_var.set("PORO")
        self.app.distance_var.set("2")
        self.app.speed_var.set("1")
        self.app.start_button.invoke()
        self.motor.queue_line("DONE\n")
        self.pump()
        self.assertTrue(self.app.is_running)
        self.assertEqual(self.app._state, "HOLDING")
        self.loadcell.queue_line("-3.0\n")
        self.loadcell.queue_line("-2.5\n")
        self.pump()
        self.assertEqual(len(self.app.force_data), 2)
        self.assertEqual(self.app.dist_data[-1], 2.0)
        self.app.stop_button.invoke()
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.motor.writes[-1], b"STOP\n")

    def test_emergency_and_motor_power(self):
        self.app.start_button.invoke()
        self.app.estop_button.invoke()
        self.assertEqual(self.motor.writes[-1], b"STOP\n")
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.app._state, "E-STOP")
        self.app.motor_off_button.invoke()
        self.assertEqual(self.motor.writes[-1], b"MOFF\n")
        self.assertFalse(self.app.motor_enabled)
        self.app.motor_on_button.invoke()
        self.assertEqual(self.motor.writes[-1], b"MON\n")
        self.assertTrue(self.app.motor_enabled)

    def test_hardware_stop_button_ends_test(self):
        self.app.start_button.invoke()
        self.motor.queue_line("STOP_BUTTON\n")
        self.pump()
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.app._state, "STOPPED")

    def test_zero_and_theme_persist(self):
        self.loadcell.queue_line("-5.0\n")
        self.pump()
        self.app.zero_button.invoke()
        self.loadcell.queue_line("-5.0\n")
        self.pump()
        self.assertEqual(self.app.force_label.cget("text"), "0.0000 N")
        self.app.theme_button.invoke()
        self.assertEqual(load_settings(Path(self.tmp.name) / "s.json").theme, "light")


if __name__ == "__main__":
    unittest.main()
