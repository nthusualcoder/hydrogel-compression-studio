import tempfile
import time
import tkinter as tk
import types
import unittest
from pathlib import Path
from unittest import mock

from hydrogel_app import updater
from hydrogel_app.app import HydrogelStudio
from hydrogel_app.config import AppSettings, load_settings, save_settings
from hydrogel_app.layout import DEFAULT_SIZE, FRAME_H, FRAME_W, MIN_SIZE, fit_window, work_area
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
        self.assertEqual(jog_command("JU", "0.5"), b"JU,0.5\n")
        self.assertEqual(jog_command("JD", 1), b"JD,1\n")
        self.assertEqual(start_command("5.0", "0.5"), b"start,5,0.5\n")
        self.assertEqual(stop_command(), b"STOP\n")
        self.assertEqual(motor_off_command(), b"MOFF\n")
        self.assertEqual(motor_on_command(), b"MON\n")
        with self.assertRaises(ValueError):
            start_command("0", "1")
        with self.assertRaises(ValueError):
            jog_command("JU", "abc")

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
            defaults = AppSettings(motor_port="COM10", indicator_port="COM11")
            self.assertEqual(load_settings(path, defaults).motor_port, "COM10")
            save_settings(AppSettings("COM7", "COM8", d, "light", "1", "2", "3"), path)
            loaded = load_settings(path, defaults)
            self.assertEqual((loaded.motor_port, loaded.theme, loaded.speed), ("COM7", "light", "3"))


class UpdaterTests(unittest.TestCase):
    """exe 교체 배치와 다운로드 검사. 실제 교체는 frozen exe에서만 일어나므로 여기서는 배치 내용과 실행 인자만 본다."""

    @staticmethod
    def response(body, declared):
        chunks = [body, b""]
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.headers = {"Content-Length": str(declared)}
        resp.read.side_effect = lambda _n: chunks.pop(0)
        return resp

    def test_swap_waits_for_bootloader_parent_and_resets_pyinstaller_env(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("hydrogel_app.updater.tempfile.gettempdir", return_value=tmp), \
                mock.patch("hydrogel_app.updater.os.getppid", return_value=4242), \
                mock.patch("hydrogel_app.updater.subprocess.Popen") as popen:
            updater.replace_and_restart(Path(tmp) / "new.exe", Path(tmp) / "cur.exe")
            batch = (Path(tmp) / "hydrogel_update.bat").read_text(encoding="mbcs")
        self.assertIn("PID eq 4242", batch)
        self.assertEqual(popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_download_rejects_body_shorter_than_content_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "new.exe"
            with mock.patch("hydrogel_app.updater.urllib.request.urlopen", return_value=self.response(b"x" * 1000, 5000)):
                with self.assertRaises(OSError):
                    updater.download("https://example.invalid/a.exe", dest)
            self.assertFalse(dest.exists())

    def test_download_accepts_complete_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "new.exe"
            with mock.patch("hydrogel_app.updater.urllib.request.urlopen", return_value=self.response(b"x" * 5000, 5000)):
                self.assertEqual(updater.download("https://example.invalid/a.exe", dest), dest)
            self.assertEqual(dest.read_bytes(), b"x" * 5000)


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

    def test_update_check_failure_reaches_handler(self):
        self.app.update_button.configure(state=tk.DISABLED)
        with mock.patch("hydrogel_app.app.updater.fetch_latest", side_effect=OSError("offline")):
            self.app._check_update_worker()
        self.pump()
        self.assertEqual(str(self.app.update_button.cget("state")), tk.NORMAL)
        self.assertIn("offline", self.app.sb_message.cget("text"))

    def test_start_point_fixed_speed_threshold_stop_then_zero(self):
        a = self.app
        self.assertEqual(a.start_force_var.get(), "0.01")
        a.jog_speed_var.set("5")
        self.loadcell.queue_line("0.0\n"); self.pump()
        a.start_point_button.invoke()
        self.assertEqual(self.motor.writes[-1], b"JU,0.01\n")
        self.loadcell.queue_line("1.0\n"); self.pump()
        self.assertEqual(a._starting_point, "seeking")  # 1 g = 0.00980665 N < 0.01 N
        self.loadcell.queue_line("1.1\n"); self.pump()
        self.assertEqual(self.motor.writes[-1], b"STOP\n")
        self.assertEqual(a._starting_point, "stopping")
        self.assertEqual(a.force_offset_N, 0.0)
        self.motor.queue_line("STOPPED\n"); self.pump()
        self.assertEqual(a._starting_point, "zeroing")
        self.loadcell.queue_line("1.2\n"); self.pump()
        self.assertIsNone(a._starting_point)
        self.assertEqual(a._state, "START SET")
        self.assertAlmostEqual(a.force_offset_N, grams_to_newton(1.2))
        self.assertEqual(a.force_label.cget("text"), "0.0000 N")
        self.assertIsNone(a.csv_path)
        self.assertFalse(a.is_running)
        self.assertEqual(str(a.start_point_button.cget("state")), tk.NORMAL)

    def test_start_point_custom_threshold_uses_displayed_force_and_saves(self):
        a = self.app
        a._on_loadcell_line("10")
        a.zero_force()
        a.start_force_var.set("0.02")
        a.find_start_point()
        self.assertEqual(load_settings(a.config_path).start_force, "0.02")
        a._on_loadcell_line("12")
        self.assertEqual(a._starting_point, "seeking")
        a._on_loadcell_line("12.1")
        self.assertEqual(a._starting_point, "stopping")
        self.assertAlmostEqual(a.force_offset_N, grams_to_newton(10))

    def test_start_point_rejects_bad_threshold_or_missing_data(self):
        a = self.app
        a.find_start_point()
        self.assertEqual(self.motor.writes, [])
        a._on_loadcell_line("0")
        for value in ("0", "-0.01", "nan", "inf", "abc", "20"):
            with self.subTest(value=value):
                a.start_force_var.set(value)
                a.find_start_point()
                self.assertIsNone(a._starting_point)
                self.assertEqual(self.motor.writes, [])
        a.start_force_var.set("0.01")
        a._last_loadcell_at -= 3
        a.find_start_point()
        self.assertEqual(self.motor.writes, [])
        a._on_loadcell_line("0")
        self.loadcell.is_open = False
        a.find_start_point()
        self.assertEqual(self.motor.writes, [])

    def test_start_point_already_loaded_does_not_advance(self):
        a = self.app
        a._on_loadcell_line("2")
        a.find_start_point()
        self.assertEqual(self.motor.writes, [b"STOP\n"])
        self.assertEqual(a._starting_point, "stopping")

    def test_start_point_blocks_other_motion_and_zeroing(self):
        a = self.app
        a._on_loadcell_line("0")
        a.find_start_point()
        sent = list(self.motor.writes)
        for fn in (a.start_test, lambda: a.start_jog("JD"), a.zero_force, a.motor_on, a.find_start_point, a.check_for_update):
            fn()
        self.assertEqual(self.motor.writes, sent)
        self.assertFalse(a.is_running)
        self.assertIsNone(a.csv_path)
        self.assertEqual(a.force_offset_N, 0.0)
        for widget in (a.start_button, a.zero_button, a.start_force_entry, a.start_point_button):
            self.assertEqual(str(widget.cget("state")), tk.DISABLED)
        a.stop_test()
        a.start_test()
        self.assertTrue(a.is_running)
        sent = list(self.motor.writes)
        a.find_start_point()
        self.assertEqual(self.motor.writes, sent)

    def test_start_point_cancel_paths_do_not_zero(self):
        a = self.app
        for stop in (a.stop_test, a.emergency_stop, a.motor_off, lambda: a._on_motor_line("STOP_BUTTON"), a.disconnect_devices):
            with self.subTest(stop=stop):
                a._on_loadcell_line("0")
                a.find_start_point()
                a._on_loadcell_line("1.1")
                stop()
                self.assertIsNone(a._starting_point)
                a._on_motor_line("STOPPED")
                a._on_loadcell_line("2")
                self.assertEqual(a.force_offset_N, 0.0)

    def test_start_point_stale_data_or_missing_stop_ack_aborts(self):
        a = self.app
        a._on_loadcell_line("0")
        a.find_start_point()
        a._last_loadcell_at -= 3
        a._check_start_point()
        self.assertIsNone(a._starting_point)
        self.assertEqual(self.motor.writes[-1], b"STOP\n")
        a._on_loadcell_line("0")
        a.find_start_point()
        a._on_loadcell_line("1.1")
        a._start_phase_at -= 3
        a._check_start_point()
        self.assertIsNone(a._starting_point)
        self.assertEqual(a.force_offset_N, 0.0)

    def test_start_point_overload_serial_error_and_stale_packets_abort(self):
        a = self.app
        for reason in ("overload", "serial_error", "stale_packet", "closed_port"):
            with self.subTest(reason=reason):
                a._on_loadcell_line("0")
                a.find_start_point()
                if reason == "overload":
                    a._on_loadcell_line("1900")
                elif reason == "serial_error":
                    a.rx_queue.put(("serial_error", "loadcell: disconnected", time.monotonic()))
                    a._pump_queue()
                elif reason == "stale_packet":
                    a._on_loadcell_line("2", time.monotonic() - 3)
                else:
                    self.loadcell.is_open = False
                    a._check_start_point()
                self.assertIsNone(a._starting_point)
                self.assertEqual(self.motor.writes[-1], b"STOP\n")
                self.assertEqual(a.force_offset_N, 0.0)

    def test_start_point_ignores_old_stop_ack_and_pre_stop_load(self):
        a = self.app
        a._on_loadcell_line("0")
        a.find_start_point()
        a._on_loadcell_line("1.1")
        a._on_motor_line("STOPPED", a._start_phase_at - 1)
        self.assertEqual(a._starting_point, "stopping")
        a._on_motor_line("STOPPED")
        a._on_loadcell_line("1.2", a._start_phase_at - 0.1)
        self.assertEqual(a._starting_point, "zeroing")
        self.assertEqual(a.force_offset_N, 0.0)
        a._on_loadcell_line("1.3")
        self.assertIsNone(a._starting_point)
        self.assertAlmostEqual(a.force_offset_N, grams_to_newton(1.3))

    def test_start_point_demo_completes_stop_and_zero(self):
        a = self.app
        a.disconnect_devices()
        a.demo = True
        a.connect_devices()
        a.demo_rig.position_mm = 0.23  # 접촉 직전: 약 0.96 g
        self.pump(0.2)
        a.find_start_point()
        deadline = time.monotonic() + 4
        while a._starting_point and time.monotonic() < deadline:
            self.pump(0.1)
        self.assertIsNone(a._starting_point)
        self.assertEqual(a._state, "START SET")
        self.assertEqual(a.demo_rig.velocity, 0.0)
        self.assertIn(b"JU,0.01\n", a.arduino.writes)
        self.assertEqual(a.arduino.writes[-1], b"STOP\n")
        self.assertGreater(a.force_offset_N, 0)
        self.assertLess(abs(float(a.force_label.cget("text").split()[0])), 0.002)

    def test_download_failure_reaches_handler(self):
        class RunNow:       # mainloop 밖에서는 다른 스레드가 after를 쓸 수 없으므로 바로 실행한다
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        self.app._update_info = {"tag": "v9.9.9"}
        with mock.patch("hydrogel_app.app.threading.Thread", RunNow), \
                mock.patch("hydrogel_app.app.updater.running_exe", return_value=None), \
                mock.patch("hydrogel_app.app.updater.download", side_effect=OSError("cut")):
            self.app._start_download({"url": "https://example.invalid/a.exe"})
        self.pump()
        self.assertEqual(str(self.app.update_button.cget("state")), tk.NORMAL)
        self.assertIn("cut", self.app.sb_message.cget("text"))

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
        self.assertAlmostEqual(self.app.force_data[-1], grams_to_newton(-25.0), places=6)
        self.app.stop_button.invoke()        # 종료 시 남은 버킷은 없음
        self.assertEqual(len(self.app.force_data), 1)

    def test_updater_version_compare(self):
        from hydrogel_app.updater import is_newer, parse_version
        self.assertEqual(parse_version("v3.1.2"), (3, 1, 2))
        self.assertTrue(is_newer("v3.0.1", "3.0.0"))
        self.assertFalse(is_newer("v3.0.0", "3.0.0"))
        self.assertTrue(is_newer("v4.0.0", "3.9.9"))

    def test_motion_profile(self):
        from hydrogel_app.motion import move_duration, position_at
        a = self.app.profile.accel_mm_s2
        # 사다리꼴: 2 mm @ 1 mm/s → 가속 0.4 s 동안 0.2 mm, 총 2.4 s
        self.assertAlmostEqual(move_duration(2.0, 1.0, a), 2.0 / 1.0 + 1.0 / a, places=9)
        self.assertAlmostEqual(position_at(0.4, 2.0, 1.0, a), 0.5 * a * 0.4 ** 2, places=9)
        self.assertAlmostEqual(position_at(1.0, 2.0, 1.0, a), 0.2 + 0.6, places=9)
        self.assertAlmostEqual(position_at(99.0, 2.0, 1.0, a), 2.0, places=9)
        self.assertLess(position_at(1.0, 2.0, 1.0, a), 1.0)          # 옛 계산(t×v)보다 뒤처진다
        # 삼각형: 0.1 mm @ 5 mm/s → 최고 속도 미도달, 총 2·sqrt(d/a)
        self.assertAlmostEqual(move_duration(0.1, 5.0, a), 2 * (0.1 / a) ** 0.5, places=9)
        # 단조 증가
        prev = 0.0
        for i in range(1, 60):
            cur = position_at(i * 0.05, 2.0, 1.0, a)
            self.assertGreaterEqual(cur, prev - 1e-12); prev = cur

    def test_speed_above_firmware_limit_rejected(self):
        vmax = self.app.profile.max_speed_mm_s
        self.app.speed_var.set(str(vmax * 2))
        self.app.start_button.invoke()
        self.assertFalse(self.app.is_running)
        self.app.jog_speed_var.set(str(vmax * 2))
        n = len(self.motor.writes)
        self.app.start_jog(self.app.profile.press_cmd)
        self.assertEqual(len(self.motor.writes), n)          # 조그 명령이 나가지 않았다
        self.app.speed_var.set(str(vmax))                    # 상한값은 허용
        self.app.start_button.invoke()
        self.assertTrue(self.app.is_running)
        self.app.stop_button.invoke()

    def test_zero_blocked_while_running(self):
        self.loadcell.queue_line("-5.0\n"); self.pump()
        self.app.start_button.invoke()
        before = self.app.force_offset_N
        self.app.zero_button.invoke()
        self.assertEqual(self.app.force_offset_N, before)
        self.app.stop_button.invoke()

    def test_bucket_averages_time_distance_force(self):
        self.app.start_button.invoke()
        self.app._bucket = [(0.1, 0.10, 1.0), (0.3, 0.30, 3.0)]
        self.app._flush_bucket()
        self.assertAlmostEqual(self.app.time_data[-1], 0.2)
        self.assertAlmostEqual(self.app.dist_data[-1], 0.2)
        self.assertAlmostEqual(self.app.force_data[-1], 2.0)
        self.app.stop_button.invoke()

    def test_recorded_distance_follows_profile_not_linear(self):
        """기록된 거리는 t×v 가 아니라 가속 프로파일 위치여야 한다."""
        from hydrogel_app.motion import position_at
        self.app.distance_var.set("2"); self.app.speed_var.set("1")
        self.app.start_button.invoke()
        self.app.start_time -= 1.0                          # 시험 시작 1 s 뒤인 것처럼
        self.loadcell.queue_line("-10.0\n"); self.pump(0.05)
        d = self.app.dist_data[-1]
        self.assertAlmostEqual(d, position_at(self.app.time_data[-1], 2.0, 1.0, self.app.profile.accel_mm_s2), places=6)
        self.assertLess(d, self.app.time_data[-1] * 1.0)
        self.app.stop_button.invoke()

    def test_csv_is_flushed_during_test(self):
        self.app.start_button.invoke()
        self.loadcell.queue_line("-10.0\n"); self.pump()
        lines = self.app.csv_path.read_text(encoding="utf-8-sig").splitlines()
        self.assertGreaterEqual(len(lines), 3)              # 메타 + 헤더 + 데이터 한 줄이 이미 디스크에 있다
        self.app.stop_button.invoke()

    def test_jog_uses_up_down_commands(self):
        self.app.jog_speed_var.set("0.5")
        self.click_hold(self.app.press_button)
        self.click_hold(self.app.release_button)
        self.assertEqual(self.motor.writes, [b"JU,0.5\n", b"STOP\n", b"JD,0.5\n", b"STOP\n"])

    def test_compression_ends_on_done_and_force_sign(self):
        self.app.distance_var.set("2")
        self.app.speed_var.set("1")
        self.app.start_button.invoke()
        self.assertEqual(self.motor.writes[-1], b"start,2,1\n")
        self.loadcell.queue_line("   +10.0\r\n")     # 입력 부호 유지: 양수 g → +N
        self.pump()
        self.assertAlmostEqual(self.app.force_data[-1], grams_to_newton(10.0), places=6)
        self.motor.queue_line("DONE\n")
        self.pump()
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.app._state, "DONE")
        csv_files = list(Path(self.tmp.name).glob("*.csv"))
        self.assertEqual(len(csv_files), 1)
        text = csv_files[0].read_text(encoding="utf-8-sig").splitlines()
        self.assertTrue(text[0].startswith("# specimen: cylinder, diameter_mm=10, height0_mm=5, area_mm2=78.5398, mode=COMPRESSION, stress=engineering F/A0"))
        self.assertEqual(text[1], "Time (s),Distance (mm),Force (N),Strain (-),Stress (kPa)")
        self.assertEqual(len(text), 3)
        self.assertAlmostEqual(float(text[2].split(",")[2]), round(grams_to_newton(10.0), 4))

    def test_loadcell_preserves_both_signs_and_zero_offset(self):
        for raw in (-10.0, 10.0):
            with self.subTest(raw=raw):
                self.app.force_offset_N = 0.0
                self.app._on_loadcell_line(f"{raw:+.1f}")
                self.assertEqual(self.app.force_label.cget("text"), f"{grams_to_newton(raw):.4f} N")
                self.app.zero_force()
                self.assertAlmostEqual(self.app.force_offset_N, grams_to_newton(raw))
                self.app._on_loadcell_line(f"{raw:+.1f}")
                self.assertEqual(self.app.force_label.cget("text"), "0.0000 N")
                self.app._on_loadcell_line(f"{raw + 5:+.1f}")
                self.assertEqual(self.app.force_label.cget("text"), f"{grams_to_newton(5):.4f} N")
        self.app.force_offset_N = 0.0
        self.app.start_button.invoke()
        self.app._on_loadcell_line("-10.0")
        self.assertAlmostEqual(self.app.force_data[-1], grams_to_newton(-10.0))
        self.assertLess(self.app.stress_data[-1], 0)
        self.app.stop_button.invoke()
        row = self.app.csv_path.read_text(encoding="utf-8-sig").splitlines()[2].split(",")
        self.assertAlmostEqual(float(row[2]), round(grams_to_newton(-10.0), 4))

    def test_stress_strain_and_modulus(self):
        import math
        self.app.diameter_var.set("10")
        self.app.height_var.set("5")
        self.app.distance_var.set("1")
        self.app.speed_var.set("5")
        self.app.start_button.invoke()
        self.app.start_time -= 100                # 이미 목표 거리에 도달한 것처럼 → strain 0.2 고정
        self.assertAlmostEqual(self.app.area_mm2, math.pi * 25, places=6)
        self.loadcell.queue_line("+10.0\n")     # 0.0980665 N
        self.pump()
        self.assertAlmostEqual(self.app.strain_data[-1], 0.2, places=6)
        expected_kPa = 0.0980665 / (math.pi * 25e-6) / 1000
        self.assertAlmostEqual(self.app.stress_data[-1], expected_kPa, places=4)
        self.assertEqual(self.app.plot_var.get(), "STRESS")
        # 탄성률: 5~15 % 구간을 수동으로 채워 직선 기울기 검증 (stress = 50 kPa * strain)
        self.app.strain_data[:] = [0.05, 0.075, 0.10, 0.125, 0.15]
        self.app.stress_data[:] = [50 * e for e in self.app.strain_data]
        self.app._update_modulus()
        self.assertAlmostEqual(self.app.modulus_kPa, 50.0, places=6)
        self.app.stop_button.invoke()

    def test_plot_labels_follow_mode(self):
        self.assertEqual(self.app.plot_radios["FORCE"].cget("text"), "힘-거리")
        self.assertEqual(self.app.plot_radios["STRESS"].cget("text"), "응력-변형률")
        self.app.mode_var.set("PORO")
        self.app.on_mode_change()
        self.assertEqual(self.app.plot_radios["FORCE"].cget("text"), "힘-시간")
        self.assertEqual(self.app.plot_radios["STRESS"].cget("text"), "응력-시간")
        self.assertEqual(self.app.axes.get_title(loc="left"), "Poroelasticity / Stress relaxation  ·  Stress vs Time")
        self.assertEqual(self.app.axes.get_xlabel(), "Time (s)")
        self.app.plot_var.set("FORCE")
        self.app._on_plot_change()
        self.assertEqual(self.app.axes.get_title(loc="left"), "Poroelasticity / Stress relaxation  ·  Force vs Time")
        self.assertEqual(self.app.axes.get_ylabel(), "Force (N)")

    def test_mode_change_blocked_while_running(self):
        self.app.start_button.invoke()
        self.assertTrue(self.app.is_running, "기본값(거리 2 mm, 높이 5 mm)으로 시작할 수 있어야 한다")
        self.app.mode_var.set("PORO")
        self.app.on_mode_change()
        self.assertEqual(self.app.mode_var.get(), "COMPRESSION")
        self.app.stop_button.invoke()

    def test_live_stress_updates_after_test_ends(self):
        """압축 종료 후에도 힘이 바뀌면 응력 표시가 함께 바뀌어야 한다 (이전 버그)."""
        self.app.diameter_var.set("10"); self.app.height_var.set("5")
        self.app.distance_var.set("1"); self.app.speed_var.set("5")
        self.app.start_button.invoke()
        self.loadcell.queue_line("-100.0\n"); self.pump()
        self.motor.queue_line("DONE\n"); self.pump()
        self.assertFalse(self.app.is_running)
        before = self.app.stress_label.cget("text")
        self.loadcell.queue_line("-10.0\n"); self.pump()      # 힘이 1/10로 감소
        after = self.app.stress_label.cget("text")
        self.assertNotEqual(before, after)
        self.assertAlmostEqual(float(after.split()[0]), float(before.split()[0]) / 10, places=2)

    def test_eta_and_progress(self):
        self.app.distance_var.set("3"); self.app.speed_var.set("0.5")
        self.assertIn("6.2 s", self.app.eta_label.cget("text"))     # 3/0.5 + 0.5/2.5 (가속·감속 포함)
        self.app.speed_var.set("0.01")
        self.assertIn("5.0 min", self.app.eta_label.cget("text"))
        self.app.distance_var.set("1"); self.app.speed_var.set("5")
        self.app.start_button.invoke()
        self.motor.queue_line("DONE\n"); self.pump()
        self.assertEqual(self.app.progress_var.get(), 100.0)
        self.assertTrue(self.app.percent_label.cget("text").startswith("100 %"))

    def test_start_rejects_bad_specimen(self):
        self.app.diameter_var.set("0")
        self.app.start_button.invoke()
        self.assertFalse(self.app.is_running)
        self.app.diameter_var.set("10")
        self.app.height_var.set("5")
        self.app.distance_var.set("5")          # 높이 이상 → 거부
        self.app.start_button.invoke()
        self.assertFalse(self.app.is_running)

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

    def test_missing_done_falls_back_to_timer(self):
        self.app.distance_var.set("0.1")
        self.app.speed_var.set("5")           # 0.1 mm 삼각형 프로파일 0.4 s → 보조 판정 약 2.6 s 후
        self.app.start_button.invoke()
        self.pump(3.2)
        self.assertFalse(self.app.motor_moving)
        self.assertEqual(self.app._state, "DONE")

    def test_zero_and_theme_persist(self):
        self.loadcell.queue_line("-5.0\n")
        self.pump()
        self.app.zero_button.invoke()
        self.loadcell.queue_line("-5.0\n")
        self.pump()
        self.assertEqual(self.app.force_label.cget("text"), "0.0000 N")
        self.app.theme_button.invoke()
        self.assertEqual(load_settings(Path(self.tmp.name) / "s.json").theme, "light")


class SmallScreenTests(unittest.TestCase):
    """화면이 작은 노트북: 창은 작업 영역에 들어가고, 패널은 스크롤되며, 안전 버튼은 항상 보인다."""

    AREA = (0, 0, 1366, 728)

    def setUp(self):
        self.patcher = mock.patch("hydrogel_app.app.work_area", return_value=self.AREA)
        self.patcher.start()
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
        self.root.deiconify()
        self.root.update()
        self.canvas = self.app.sidebar_canvas

    def tearDown(self):
        self.patcher.stop()
        self.settle()                       # 남은 그리기·테마 예약을 닫기 전에 처리한다
        self.app.close()
        self.tmp.cleanup()

    def settle(self):
        for _ in range(3):
            self.root.update()

    def resize(self, w, h):
        """창 크기를 바꾼다. Windows는 실제 화면보다 큰 창을 만들지 않으므로 그런 PC에서는 건너뛴다."""
        max_w, max_h = self.root.maxsize()
        if w > max_w or h > max_h:
            self.skipTest(f"실제 화면이 {w}x{h} 창보다 작다")
        self.root.geometry(f"{w}x{h}")
        self.settle()
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (w, h))

    def scroll_to(self, fraction):
        self.canvas.yview_moveto(fraction)
        self.settle()

    def view_top(self):
        return self.canvas.canvasy(0)

    def wheel(self, delta, x=None, y=None, widget=None):
        c = self.canvas
        self.app._on_mousewheel(types.SimpleNamespace(
            delta=delta,
            x_root=c.winfo_rootx() + 30 if x is None else x,
            y_root=c.winfo_rooty() + 30 if y is None else y,
            widget=c if widget is None else widget,
        ))

    def in_viewport(self, widget):
        c = self.canvas
        top = widget.winfo_rooty()
        return c.winfo_rooty() <= top and top + widget.winfo_height() <= c.winfo_rooty() + c.winfo_height()

    def font_size(self, widget):
        return int(self.root.tk.splitlist(widget.cget("font"))[1])

    # ---- 창 크기
    def test_fit_window_pure(self):
        self.assertEqual(fit_window((0, 0, 3840, 2160))[:2], DEFAULT_SIZE)
        self.assertEqual(fit_window((0, 0, 1366, 728))[:2], (1350, 688))
        self.assertEqual(fit_window((0, 0, 1024, 560))[:2], (1008, 520))
        w, h, _x, _y, min_w, min_h = fit_window((0, 0, 800, 480))
        self.assertLessEqual(min_w, w)
        self.assertLessEqual(min_h, h)
        self.assertGreaterEqual(fit_window((48, 0, 1366, 768))[2], 48)      # 왼쪽 작업 표시줄
        for area in ((0, 0, 3840, 2160), (0, 0, 1920, 1040), (0, 0, 1536, 824), (0, 0, 1366, 728), (0, 0, 1280, 680),
                     (0, 0, 1024, 560), (0, 0, 800, 480), (48, 0, 1366, 768), (0, 40, 1366, 768), (-1920, 0, 0, 1040)):
            with self.subTest(area=area):
                left, top, right, bottom = area
                w, h, x, y, min_w, min_h = fit_window(area)
                self.assertGreaterEqual(x, left)
                self.assertGreaterEqual(y, top)
                self.assertLessEqual(x + w + FRAME_W, right)
                self.assertLessEqual(y + h + FRAME_H, bottom)
                self.assertEqual((min_w, min_h), (min(MIN_SIZE[0], w), min(MIN_SIZE[1], h)))

    def test_work_area_falls_back_to_screen_size(self):
        fake_root = types.SimpleNamespace(winfo_screenwidth=lambda: 1280, winfo_screenheight=lambda: 720)
        expected = (0, 0, 1280, 672)
        def raises(*_a):
            raise OSError("no")
        for windll in (types.SimpleNamespace(),     # AttributeError (windll에 user32 없음)
                       types.SimpleNamespace(user32=types.SimpleNamespace(SystemParametersInfoW=raises)),    # OSError
                       types.SimpleNamespace(user32=types.SimpleNamespace(SystemParametersInfoW=lambda *_a: 0))):  # 0 반환
            with mock.patch("hydrogel_app.layout.ctypes.windll", windll):
                self.assertEqual(work_area(fake_root), expected)

    def test_work_area_ignores_values_larger_than_tk_screen(self):
        fake_root = types.SimpleNamespace(winfo_screenwidth=lambda: 1280, winfo_screenheight=lambda: 720)
        def windll_returning(right, bottom, left=0):
            def spi(_action, _param, rect_ref, _flags):
                rect_ref._obj.left, rect_ref._obj.right, rect_ref._obj.bottom = left, right, bottom
                return 1
            return types.SimpleNamespace(user32=types.SimpleNamespace(SystemParametersInfoW=spi))
        with mock.patch("hydrogel_app.layout.ctypes.windll", windll_returning(1920, 1040)):     # 물리 좌표
            self.assertEqual(work_area(fake_root), (0, 0, 1280, 672))
        with mock.patch("hydrogel_app.layout.ctypes.windll", windll_returning(1280, 680)):      # 같은 좌표계
            self.assertEqual(work_area(fake_root), (0, 0, 1280, 680))
        with mock.patch("hydrogel_app.layout.ctypes.windll", windll_returning(1280, 720, left=62)):     # 왼쪽 작업 표시줄: 아래가 화면과 같아도 받는다
            self.assertEqual(work_area(fake_root), (62, 0, 1280, 720))
        for right, bottom in ((1280, 1040), (1920, 680)):       # 한 축만 커도 버린다
            with mock.patch("hydrogel_app.layout.ctypes.windll", windll_returning(right, bottom)):
                self.assertEqual(work_area(fake_root), (0, 0, 1280, 672))

    def test_real_window_fits_area(self):
        left, top, right, bottom = self.AREA
        r = self.root
        self.assertLessEqual(r.winfo_width() + FRAME_W, right - left)
        self.assertLessEqual(r.winfo_height() + FRAME_H, bottom - top)
        self.assertGreaterEqual(r.winfo_rootx(), left)
        self.assertGreaterEqual(r.winfo_rooty(), top)
        self.assertLessEqual(r.winfo_rootx() + r.winfo_width(), right)
        self.assertLessEqual(r.winfo_rooty() + r.winfo_height(), bottom)
        sb = self.app.statusbar
        self.assertLessEqual(sb.winfo_rooty() + sb.winfo_height(), bottom)   # 상태 바가 화면 밖으로 나가지 않는다
        min_w, min_h = r.minsize()
        self.assertLessEqual(min_w, r.winfo_width())
        self.assertLessEqual(min_h, r.winfo_height())

    # ---- 스크롤 막대와 내용
    def test_scrollbar_only_when_needed(self):
        self.assertTrue(self.app.sidebar_scroll.winfo_ismapped())
        self.scroll_to(1.0)
        self.assertGreater(self.view_top(), 0)
        self.resize(1360, 1100)
        self.assertFalse(self.app.sidebar_scroll.winfo_ismapped())
        self.assertEqual(self.canvas.yview(), (0.0, 1.0))
        self.resize(1350, 688)
        self.assertTrue(self.app.sidebar_scroll.winfo_ismapped())

    def test_scrollbar_does_not_cover_widgets(self):
        """막대는 오른쪽 16 px 여백 위에만 겹친다. 막대가 나타나도 내용 폭은 그대로다."""
        bar = self.app.sidebar_scroll
        width_with_bar = self.app.sidebar_inner.winfo_width()
        self.assertEqual(bar.winfo_width(), 10)
        self.assertEqual(width_with_bar, self.canvas.winfo_width())
        for w in self.app._entries + [self.app.press_button, self.app.release_button, self.app.sample_rate_box, self.app.dir_button]:
            self.assertLessEqual(w.winfo_rootx() + w.winfo_width(), bar.winfo_rootx())
        self.resize(1360, 1100)
        self.assertEqual(self.app.sidebar_inner.winfo_width(), width_with_bar)

    def test_scrollbar_thumb_and_trough_work(self):
        """화살표를 뺀 사용자 레이아웃에서도 손잡이 끌기와 트로프 클릭이 동작한다."""
        bar = self.app.sidebar_scroll
        x = bar.winfo_width() // 2
        self.assertTrue(bar.identify(x, 5).endswith("thumb"))
        bar.event_generate("<ButtonPress-1>", x=x, y=5)
        expected_shift = bar.delta(0, 100) * self.app.sidebar_inner.winfo_reqheight()
        bar.event_generate("<B1-Motion>", x=x, y=105)
        bar.event_generate("<ButtonRelease-1>", x=x, y=105)
        self.settle()
        content_h = self.app.sidebar_inner.winfo_reqheight()
        self.assertAlmostEqual(self.view_top(), expected_shift, delta=3)
        self.scroll_to(0.0)
        y = bar.winfo_height() - 3
        self.assertTrue(bar.identify(x, y).endswith("trough"))
        bar.event_generate("<ButtonPress-1>", x=x, y=y)
        bar.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.settle()
        self.assertGreater(self.view_top(), 0)

    def test_everything_reachable_at_any_height(self):
        inner = self.app.sidebar_inner
        last = inner.winfo_children()[-1]
        self.assertEqual(int(self.canvas.cget("yscrollincrement")), 1)
        for h in (420, 433, 461, 500, 537, 600, 651, 688, 733, 800):
            with self.subTest(height=h):
                self.resize(1350, h)
                self.scroll_to(1.0)
                self.assertEqual(self.canvas.yview()[1], 1.0)
                self.assertLessEqual(last.winfo_rooty() + last.winfo_height(), self.canvas.winfo_rooty() + self.canvas.winfo_height())
                # 끝까지 내리면 내용의 아래 끝과 뷰의 아래 끝이 정확히 맞는다 (못 닿는 픽셀도, 빈 틈도 없다)
                self.assertEqual(inner.winfo_rooty() + inner.winfo_height(), self.canvas.winfo_rooty() + self.canvas.winfo_height())
                self.scroll_to(0.0)
                self.assertEqual(inner.winfo_rooty(), self.canvas.winfo_rooty())

    def test_enlarging_leaves_no_gap(self):
        inner = self.app.sidebar_inner
        self.resize(1350, 500)
        self.scroll_to(1.0)
        self.resize(1350, 700)
        self.assertEqual(self.canvas.yview()[1], 1.0)
        self.assertEqual(inner.winfo_rooty() + inner.winfo_height(), self.canvas.winfo_rooty() + self.canvas.winfo_height())

    def test_footer_always_visible(self):
        a = self.app
        footer = str(a.sidebar_footer) + "."
        min_w, min_h = self.root.minsize()
        for size in ((1350, 688), (min_w, min_h)):
            self.resize(*size)
            for fraction in (0.0, 1.0):
                self.scroll_to(fraction)
                for b in (a.start_button, a.stop_button, a.estop_button, a.motor_off_button, a.motor_on_button):
                    with self.subTest(size=size, fraction=fraction, button=b.cget("text")):
                        self.assertTrue(str(b).startswith(footer))
                        self.assertTrue(b.winfo_ismapped())
                        self.assertGreaterEqual(b.winfo_rooty(), self.root.winfo_rooty())
                        self.assertLessEqual(b.winfo_rooty() + b.winfo_height(), a.statusbar.winfo_rooty())
                        self.assertGreaterEqual(b.winfo_rootx(), self.root.winfo_rootx())
                        self.assertLessEqual(b.winfo_rootx() + b.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
                        self.assertGreaterEqual(b.winfo_height(), b.winfo_reqheight())
                        self.assertGreaterEqual(b.winfo_width(), b.winfo_reqwidth())

    def test_tab_order_unchanged(self):
        """고정 영역으로 옮긴 버튼도 기존처럼 스크롤 영역 다음 순서여야 한다."""
        a = self.app
        e = a._entries
        expected = [e[0], e[1], a.disconnect_button, a.save_defaults_button, e[2], a.press_button, a.release_button,
                    a.zero_button, e[3], a.start_point_button, a._radios[0], a._radios[1], e[4], e[5], e[6], e[7], a._radios[2], a._radios[3],
                    a.sample_rate_box, e[8], a.dir_button, a.start_button, a.stop_button, a.estop_button,
                    a.motor_off_button, a.motor_on_button]
        w, order = e[0], []
        for _ in expected:
            order.append(w)
            w = w.tk_focusNext()
        self.assertEqual(order, expected)

    # ---- 마우스 휠 / 터치패드
    def test_wheel_steps(self):
        self.assertEqual(self.view_top(), 0)
        self.wheel(-120)
        self.assertEqual(self.view_top(), 60)
        self.wheel(120)
        self.assertEqual(self.view_top(), 0)
        for _ in range(4):
            self.wheel(-30)
        self.assertEqual(self.view_top(), 60)
        self.scroll_to(0.0)
        self.wheel(-15)                     # 정밀 터치패드: 한 번만 와도 움직인다
        self.assertEqual(self.view_top(), 7)
        for _ in range(7):
            self.wheel(-15)
        self.assertEqual(self.view_top(), 60)
        for _ in range(8):
            self.wheel(15)
        self.assertEqual(self.view_top(), 0)

    def test_wheel_ignored_outside_panel_and_while_jogging(self):
        a = self.app
        main, footer = a.main, a.sidebar_footer
        self.wheel(-120, x=main.winfo_rootx() + 50, y=main.winfo_rooty() + 200)
        self.assertEqual(self.view_top(), 0)
        self.wheel(-120, x=footer.winfo_rootx() + 30, y=footer.winfo_rooty() + 30)
        self.assertEqual(self.view_top(), 0)
        self.wheel(-120, widget=str(a.sample_rate_box) + ".popdown.f.l")
        self.assertEqual(self.view_top(), 0)
        a.jogging = True
        self.wheel(-120)
        self.assertEqual(self.view_top(), 0)
        a.jogging = False
        self.wheel(-120)                    # 같은 위치에서 조그가 끝나면 다시 움직인다
        self.assertEqual(self.view_top(), 60)

    def test_wheel_blocked_while_jog_button_held(self):
        a = self.app
        a.press_button.event_generate("<ButtonPress-1>", x=2, y=2)
        self.settle()
        self.assertTrue(a.jogging)
        self.wheel(-120)
        self.assertEqual(self.view_top(), 0)
        a.press_button.event_generate("<ButtonRelease-1>", x=2, y=2)
        self.settle()
        self.assertFalse(a.jogging)

    def test_wheel_noop_when_content_fits(self):
        self.resize(1360, 1100)
        self.wheel(-120)
        self.assertEqual(self.canvas.yview(), (0.0, 1.0))
        self.wheel(-15)
        self.assertEqual(self.app._wheel_acc, 0)     # 스크롤할 것이 없으면 작은 값도 모으지 않는다

    def test_wheel_over_combobox_scrolls_panel_not_value(self):
        a = self.app
        box = a.sample_rate_box
        a.sample_rate_var.set("10")
        a._scroll_into_view(box)
        self.settle()
        before = self.view_top()
        box.event_generate("<MouseWheel>", delta=-120, rootx=box.winfo_rootx() + 5, rooty=box.winfo_rooty() + 5)
        self.settle()
        self.assertEqual(a.sample_rate_var.get(), "10")
        self.assertEqual(self.view_top(), before + 60)

    def test_wheel_over_scrollbar_applied_once(self):
        bar = self.app.sidebar_scroll
        self.scroll_to(0.0)
        bar.event_generate("<MouseWheel>", delta=-120, rootx=bar.winfo_rootx() + 5, rooty=bar.winfo_rooty() + 50)
        self.settle()
        self.assertEqual(self.view_top(), 60)

    # ---- 키보드 포커스
    def test_scroll_into_view(self):
        a = self.app
        first, last = a._entries[0], a._entries[-1]
        self.resize(1350, 520)
        self.scroll_to(0.0)
        self.assertFalse(self.in_viewport(last))
        a._scroll_into_view(last)
        self.settle()
        self.assertTrue(self.in_viewport(last))
        self.scroll_to(1.0)
        self.assertFalse(self.in_viewport(first))
        a._scroll_into_view(first)
        self.settle()
        self.assertTrue(self.in_viewport(first))
        self.assertEqual(self.view_top(), first.winfo_rooty() - a.sidebar_inner.winfo_rooty() - 8)     # 위쪽 여백 8 px
        before = self.view_top()            # 이미 보이는 위젯이면 움직이지 않는다
        a._scroll_into_view(first)
        self.assertEqual(self.view_top(), before)
        self.resize(1360, 1100)             # 내용이 다 들어가면 아무 일도 하지 않는다
        a._scroll_into_view(last)
        self.assertEqual(self.canvas.yview(), (0.0, 1.0))

    def test_focus_scrolls_widget_into_view(self):
        last = self.app._entries[-1]
        self.resize(1350, 520)
        self.scroll_to(0.0)
        self.assertFalse(self.in_viewport(last))
        last.focus_force()
        self.settle()
        self.assertTrue(self.in_viewport(last))

    def test_focus_on_visible_widget_does_not_move_panel(self):
        """FocusIn은 조상 Frame에도 온다. 이미 보이는 입력칸을 눌렀다고 패널이 움직이면 두 번째 클릭이 다른 버튼에 맞는다."""
        a = self.app
        self.resize(1350, 688)
        for entry in a._entries:
            self.root.focus_force()
            self.settle()
            self.scroll_to(0.0)
            if self.in_viewport(entry):
                entry.focus_force()
                self.settle()
                self.assertEqual(self.view_top(), 0)
        box = a.sample_rate_box             # 콤보박스는 목록을 먼저 펼치므로 패널이 움직이면 목록이 칸에서 떨어진다
        self.root.focus_force()
        self.settle()
        a._scroll_into_view(box)
        self.settle()
        before = self.view_top()
        box.focus_force()
        self.settle()
        self.assertEqual(self.view_top(), before)

    def test_wheel_hiding_focused_entry_drops_focus(self):
        """휠로 입력칸이 가려지면 포커스가 빠져 숨은 값에 글자가 들어가지 않고 방향키 조그가 동작한다."""
        a, p = self.app, self.app.profile
        e = a._entries[-1]                  # 파일 이름: 맨 아래까지 내렸을 때 보이고, 위로 굴리면 사라진다
        self.scroll_to(1.0)
        e.focus_force()
        self.settle()
        self.wheel(120)                     # 한 칸: 아직 전부 보이면 포커스를 유지하고 방향키는 조그가 아니다
        self.settle()
        self.assertTrue(self.in_viewport(e))
        self.assertEqual(str(self.root.tk.call("focus")), str(e))
        self.motor.writes.clear()
        self.root.event_generate(f"<KeyPress-{p.press_key}>")
        self.root.event_generate(f"<KeyRelease-{p.press_key}>")
        self.settle()
        self.assertEqual(self.motor.writes, [])
        self.wheel(120)
        self.settle()
        self.assertFalse(self.in_viewport(e))
        self.assertNotEqual(str(self.root.tk.call("focus")), str(e))
        text = e.get()
        self.root.focus_force()
        self.root.event_generate("<KeyPress-1>")
        self.settle()
        self.assertEqual(e.get(), text)
        self.root.event_generate(f"<KeyPress-{p.press_key}>")
        self.settle()
        self.assertEqual(self.motor.writes, [jog_command(p.press_cmd, a.jog_speed_var.get())])
        self.root.event_generate(f"<KeyRelease-{p.press_key}>")
        end = time.time() + 0.2             # 키 뗌은 60 ms 뒤에 처리된다
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(a.jogging)
        first = a._entries[0]               # 반대 방향: 아래로 굴려 위쪽으로 사라져도 같다
        self.scroll_to(0.0)
        first.focus_force()
        self.settle()
        self.wheel(-120)
        self.settle()
        self.assertFalse(self.in_viewport(first))
        self.assertNotEqual(str(self.root.tk.call("focus")), str(first))

    # ---- 테마
    def test_theme_recolors_scroll_parts(self):
        a = self.app
        for _ in range(2):
            a.toggle_theme()
            c = a.colors
            for w in (self.canvas, a.sidebar_inner, a.sidebar_host, a.sidebar_footer):
                self.assertEqual(w.cget("bg"), c["sidebar"])
            self.assertEqual(a.footer_sep.cget("bg"), c["border"])
            self.assertEqual(a.style.lookup("Hydro.Vertical.TScrollbar", "troughcolor"), c["sidebar"])
            self.assertEqual(a.style.lookup("Hydro.Vertical.TScrollbar", "background"), c["muted"])
            self.assertEqual(a.style.lookup("Hydro.Vertical.TScrollbar", "background", ["active"]), c["text"])

    # ---- 그래프
    def test_graph_labels_inside_figure_at_small_size(self):
        a = self.app
        for size in (self.root.minsize(), (1008, 520), (1350, 688)):
            with self.subTest(size=size):
                self.resize(*size)
                a.canvas.draw()
                renderer = a.canvas.get_renderer()
                fig = a.figure.bbox
                widget = a.canvas.get_tk_widget()
                self.assertEqual((round(fig.width), round(fig.height)), (widget.winfo_width(), widget.winfo_height()))
                x_label = a.axes.xaxis.label.get_window_extent(renderer)
                self.assertGreaterEqual(x_label.y0, 0)
                self.assertLessEqual(x_label.y1, fig.height)
                tight = a.axes.get_tightbbox(renderer)      # 제목, 축 이름, 눈금 글자 전부
                self.assertGreaterEqual(tight.x0, 0)
                self.assertGreaterEqual(tight.y0, 0)
                self.assertLessEqual(tight.x1, fig.width)
                self.assertLessEqual(tight.y1, fig.height)

    # ---- 조그 (방향키는 스크롤과 무관해야 한다)
    def test_arrow_key_jog_unchanged_and_panel_stays(self):
        a = self.app
        p = a.profile
        a.jog_speed_var.set("0.5")
        # 범위의 가운데에 둔다. 양 끝이면 그쪽으로 움직이는 스크롤을 잡아내지 못한다
        content_h = a.sidebar_inner.winfo_reqheight()
        self.scroll_to((content_h - self.canvas.winfo_height()) / 2 / content_h)
        before = self.view_top()
        self.assertGreater(before, 0)
        for key, cmd in ((p.press_key, p.press_cmd), (p.release_key, p.release_cmd)):
            with self.subTest(key=key):
                self.motor.writes.clear()
                self.root.focus_force()                 # 키 이벤트는 포커스가 앱에 있어야 전달된다
                self.root.event_generate(f"<KeyPress-{key}>")
                self.settle()
                self.assertEqual(self.motor.writes, [jog_command(cmd, "0.5")])
                self.assertTrue(a.jogging)
                self.assertEqual(self.view_top(), before)
                self.root.focus_force()
                self.root.event_generate(f"<KeyRelease-{key}>")
                end = time.time() + 0.2                 # 키 뗌은 60 ms 뒤에 처리된다
                while time.time() < end:
                    self.root.update()
                    time.sleep(0.01)
                self.assertEqual(self.motor.writes, [jog_command(cmd, "0.5"), stop_command()])
                self.assertFalse(a.jogging)
                self.assertEqual(self.view_top(), before)

    def test_stop_paths_clear_stale_jog_flag(self):
        """조그 키를 뗀 이벤트를 놓쳐도(입력칸으로 포커스 이동 등) 모터를 세운 뒤에는 휠과 포커스 스크롤이 다시 동작한다."""
        a, p = self.app, self.app.profile
        last = a._entries[-1]
        self.resize(1350, 520)
        for name, stop in (("정지 버튼", a.stop_button.invoke),
                           ("하드웨어 정지", lambda: a._on_motor_line("STOP_BUTTON")),
                           ("연결 해제", a.disconnect_devices)):      # 연결 해제는 모터가 끊기므로 마지막
            with self.subTest(path=name):
                self.scroll_to(0.0)
                a.jog_key_active = None             # 앞 경로에서 놓친 키 뗌이 남긴 값
                self.root.focus_force()
                self.root.event_generate(f"<KeyPress-{p.press_key}>")
                self.settle()
                self.assertTrue(a.jogging)
                stop()
                self.settle()
                self.assertFalse(a.jogging)
                self.wheel(-120)
                self.assertEqual(self.view_top(), 60)
                self.scroll_to(0.0)
                self.assertFalse(self.in_viewport(last))
                a._scroll_into_view(last)
                self.settle()
                self.assertTrue(self.in_viewport(last))

    def test_held_jog_key_does_not_restart_after_stop_button(self):
        """정지 버튼을 눌러도 키를 아직 누르고 있으면 자동 반복으로 조그가 다시 시작되지 않는다."""
        a, p = self.app, self.app.profile
        self.root.focus_force()
        self.root.event_generate(f"<KeyPress-{p.press_key}>")
        self.settle()
        self.assertTrue(a.jogging)
        self.motor.writes.clear()
        a.stop_button.invoke()
        self.assertEqual(self.motor.writes, [stop_command()])
        self.assertFalse(a.jogging)
        self.root.focus_force()
        self.root.event_generate(f"<KeyPress-{p.press_key}>")     # 자동 반복
        self.settle()
        self.assertEqual(self.motor.writes, [stop_command()])
        self.assertFalse(a.jogging)

    # ---- 메인 상단 지표 (4층만 축소 표시)
    def test_compact_mode_follows_window_and_restores(self):
        a = self.app
        self.resize(1350, 688)      # 이 창을 만들 수 없는 작은 화면에서는 건너뛴다
        original = {"force_label": 34, "peak_label": 18, "stress_label": 18, "progress_label": 12, "modulus_label": 12, "percent_label": 11}
        compact = {"force_label": 22, "peak_label": 13, "stress_label": 13, "progress_label": 10, "modulus_label": 9, "percent_label": 9}
        long_caption = a.modulus_caption.cget("text")
        self.assertIn("COMPRESSIVE MODULUS", long_caption)
        self.assertEqual({n: self.font_size(getattr(a, n)) for n in original}, original)     # 1350x688은 축소하지 않는다
        a.csv_label.configure(text="기록 중: Sample_01_20260916_115049.csv")

        for size in ((1008, 800), (1350, 600), self.root.minsize()):     # 좁아서, 낮아서, 최소 크기
            with self.subTest(size=size):
                self.resize(*size)
                self.assertTrue(a._compact)
                self.assertEqual({n: self.font_size(getattr(a, n)) for n in compact}, compact)
                self.assertEqual(a.percent_label.cget("width"), 20)
                self.assertEqual(a.modulus_caption.cget("text"), "MODULUS (5-15 %)")
                self.assertFalse(a.csv_label.winfo_ismapped())
                self.assertEqual(int(a.metric_row.cget("pady")), 6)
                self.assertEqual(int(a.metric_row.cget("padx")), 8)
                for state in ("READY", "MOTOR OFF", "RUNNING", "E-STOP"):      # 가장 긴 글자도 잘리지 않는다
                    a._set_state(state)
                    self.settle()
                    badge = a.state_badge
                    self.assertGreaterEqual(badge.winfo_width(), badge.winfo_reqwidth())
                    self.assertLessEqual(badge.winfo_rootx() + badge.winfo_width(), a.main.winfo_rootx() + a.main.winfo_width())
                a._set_state("READY")
        a.toggle_theme()                                # 축소 상태에서도 테마 전환이 동작한다
        self.assertEqual(a.force_label.cget("bg"), a.colors["bg"])
        self.assertEqual(self.font_size(a.force_label), 22)

        self.resize(1360, 800)
        self.assertFalse(a._compact)
        self.assertEqual({n: self.font_size(getattr(a, n)) for n in original}, original)
        self.assertEqual(a.percent_label.cget("width"), 26)
        self.assertEqual(a.modulus_caption.cget("text"), long_caption)
        self.assertTrue(a.csv_label.winfo_ismapped())
        self.assertEqual(int(a.metric_row.cget("pady")), 16)
        self.assertEqual(int(a.metric_row.cget("padx")), 24)
        self.assertEqual(int(a.metric_mid.pack_info()["padx"]), 40)
        self.assertEqual(tuple(int(v) for v in self.root.tk.splitlist(a.metric_mid2.pack_info()["padx"])), (0, 40))

    def test_csv_label_does_not_squeeze_live_values(self):
        """기록 중 파일 이름이 길어도 지표는 전부 보인다. 좁으면 파일 이름의 뒷부분만 보인다."""
        a = self.app
        a.csv_label.configure(text="기록 중: Sample_01_20260916_115049.csv")
        a.stress_label.configure(text="123.456 kPa  ·  25.0 %")
        for width in (1360, 1350, 1341):
            with self.subTest(width=width):
                self.resize(width, 700)
                self.assertFalse(a._compact)
                self.assertGreaterEqual(a.metric_mid2.winfo_width(), a.metric_mid2.winfo_reqwidth())
                self.assertGreaterEqual(a.state_badge.winfo_width(), a.state_badge.winfo_reqwidth())
                self.assertTrue(a.csv_label.winfo_ismapped())

    def test_running_values_not_clipped_at_any_width(self):
        """시험 중 값은 0일 때보다 넓다. 잘리면 변형률이 다른 숫자로 읽히므로(15.0 %가 1로) 어느 폭에서도 온전히 보여야 한다."""
        a = self.app
        full_scale = ("19.6133 N", "2.50 mm  ·  1234.5 s", "249.723 kPa  ·  50.0 %")     # 2 kg 로드셀 만충, 긴 유지 시간
        start = ("0.5000 N", "0.25 mm  ·  0.5 s", "6.366 kPa  ·  5.0 %")
        a._set_state("RUNNING")
        for (force, progress, stress), modulus in ((full_scale, "E = 1234.56 kPa   (1234 pts)"),
                                                   (full_scale, "-  (압축 강도 모드에서만 계산)"),
                                                   (start, "-  (5-15 % 변형률 구간 데이터 부족)")):
            a.force_label.configure(text=force)
            a.peak_label.configure(text=force)
            a.progress_label.configure(text=progress)
            a.stress_label.configure(text=stress)
            a.modulus_label.configure(text=modulus)
            for width in (self.root.minsize()[0], 1008, 1251, 1264, 1340, 1341, 1350, 1360):
                with self.subTest(width=width, stress=stress, modulus=modulus):
                    self.resize(width, 700)
                    self.assertEqual(a._compact, width < 1341)
                    for w in (a.force_label, a.peak_label, a.progress_label, a.stress_label, a.modulus_label, a.state_badge):
                        self.assertGreaterEqual(w.winfo_width(), w.winfo_reqwidth())
        a._set_state("READY")

    def test_default_size_keeps_controls_readable_and_reachable(self):
        """Starting point 추가 후에도 기본 글자 크기를 유지하고 아래쪽 설정까지 스크롤된다."""
        a = self.app
        self.resize(*DEFAULT_SIZE)
        self.assertTrue(a.sidebar_scroll.winfo_ismapped())
        self.assertFalse(a._compact)
        self.scroll_to(1.0)
        last = a.sidebar_inner.winfo_children()[-1]
        self.assertLessEqual(last.winfo_rooty() + last.winfo_height(), self.canvas.winfo_rooty() + self.canvas.winfo_height())
        self.assertEqual(self.font_size(a.force_label), 34)


if __name__ == "__main__":
    unittest.main()
