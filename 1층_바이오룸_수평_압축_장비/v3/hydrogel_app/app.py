"""Hydrogel Compression Studio v3 - Tkinter GUI (VS Code 스타일)."""
import argparse
import csv
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from . import __version__
from .config import AppSettings, default_config_path, load_settings, save_settings
from .profile import PROFILE, DeviceProfile
from .protocol import (
    grams_to_newton,
    jog_command,
    motor_off_command,
    motor_on_command,
    parse_indicator_grams,
    start_command,
    stop_command,
)
from .serial_io import DemoRig, open_serial
from .theme import THEMES
from . import updater

MOTOR_BAUDRATE = 115200
INDICATOR_BAUDRATE = 115200
UI_FONT = "Segoe UI"
SAMPLE_RATE_OPTIONS = ("0", "10", "5", "2", "1", "0.5", "0.2")   # samples/s, 0 = 원본 전부


class HydrogelStudio:
    def __init__(
        self,
        root: tk.Tk,
        profile: DeviceProfile,
        *,
        demo=False,
        config_path=None,
        serial_factory=open_serial,
        show_dialogs=True,
        initial_theme=None,
    ):
        self.root = root
        self.profile = profile
        self.demo = demo
        self.serial_factory = serial_factory
        self.show_dialogs = show_dialogs
        self.config_path = Path(config_path) if config_path else default_config_path(profile.key)
        self.settings = load_settings(
            self.config_path,
            AppSettings(
                motor_port=profile.default_motor_port,
                indicator_port=profile.default_indicator_port,
            ),
        )
        theme_name = initial_theme or self.settings.theme
        self.colors = THEMES.get(theme_name, THEMES["dark"])

        # 장치 / 상태
        self.arduino = None
        self.indicator = None
        self.demo_rig = None
        self.motor_enabled = True
        self.is_running = False          # 기록 중
        self.motor_moving = False        # 자동 이동 중
        self.jogging = False
        self.current_grams = 0.0
        self.force_offset_N = 0.0
        self.peak_force_N = 0.0
        self.start_time = 0.0
        self.expected_move_s = 0.0
        self.target_dist = 0.0
        self.test_speed = 0.0
        self.csv_file = None
        self.csv_writer = None
        self.csv_path = None
        self.time_data, self.dist_data, self.force_data = [], [], []
        self.rx_queue = queue.Queue()
        self.after_ids = []
        self.closing = False
        self.key_release_timer = None
        self.jog_key_active = None
        self._bucket = []            # 평균용 힘 버퍼
        self._bucket_t0 = 0.0        # 현재 버킷 시작 시각
        self._update_info = None

        self._build()
        self._apply_theme()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._schedule(30, self._pump_queue)
        self._schedule(80, self._update_graph)

        if self.demo:
            self.connect_devices()

    # ------------------------------------------------------------------ 구성
    def _schedule(self, ms, fn):
        if self.closing:
            return
        self.after_ids.append(self.root.after(ms, fn))

    def _build(self):
        p = self.profile
        self.root.title(f"{p.title}  ·  v{__version__}")
        self.root.geometry("1360x860")
        self.root.minsize(1180, 760)

        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")

        # ── 상단 타이틀 바
        self.titlebar = tk.Frame(self.root, height=44)
        self.titlebar.pack(fill=tk.X, side=tk.TOP)
        self.titlebar.pack_propagate(False)
        self.title_label = tk.Label(self.titlebar, text="Hydrogel Compression Studio", font=(UI_FONT, 12, "bold"))
        self.title_label.pack(side=tk.LEFT, padx=(16, 8))
        self.subtitle_label = tk.Label(self.titlebar, text=f"{p.location}   ·   {p.board}   ·   {p.loadcell}", font=(UI_FONT, 9))
        self.subtitle_label.pack(side=tk.LEFT)
        self.theme_button = tk.Button(self.titlebar, text="", command=self.toggle_theme, relief=tk.FLAT, bd=0, padx=12, cursor="hand2", font=(UI_FONT, 9))
        self.theme_button.pack(side=tk.RIGHT, padx=8, pady=8)
        self.update_button = tk.Button(self.titlebar, text=f"⬇  업데이트 확인  (v{__version__})", command=self.check_for_update, relief=tk.FLAT, bd=0, padx=12, cursor="hand2", font=(UI_FONT, 9))
        self.update_button.pack(side=tk.RIGHT, padx=(8, 0), pady=8)

        # ── 하단 상태 바
        self.statusbar = tk.Frame(self.root, height=24)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)
        self.statusbar.pack_propagate(False)
        self.sb_device = tk.Label(self.statusbar, text=f"  ⬢  {p.key}  ·  {p.location}", font=(UI_FONT, 9))
        self.sb_device.pack(side=tk.LEFT, padx=(4, 12))
        self.sb_conn = tk.Label(self.statusbar, text="○ 연결 안 됨", font=(UI_FONT, 9))
        self.sb_conn.pack(side=tk.LEFT, padx=12)
        self.sb_motor = tk.Label(self.statusbar, text="", font=(UI_FONT, 9))
        self.sb_motor.pack(side=tk.LEFT, padx=12)
        self.sb_message = tk.Label(self.statusbar, text="", font=(UI_FONT, 9), anchor="w")
        self.sb_message.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12)
        self.sb_clock = tk.Label(self.statusbar, text="", font=(UI_FONT, 9))
        self.sb_clock.pack(side=tk.RIGHT, padx=12)
        self.sb_samples = tk.Label(self.statusbar, text="", font=(UI_FONT, 9))
        self.sb_samples.pack(side=tk.RIGHT, padx=12)

        # ── 본문: 사이드바 + 메인
        body = tk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True)
        self.body = body

        self.sidebar = tk.Frame(body, width=350)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        self.sidebar_sep = tk.Frame(body, width=1)
        self.sidebar_sep.pack(side=tk.LEFT, fill=tk.Y)

        self.main = tk.Frame(body)
        self.main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_sidebar()
        self._build_main()
        self._bind_keys()

    # 사이드바 ---------------------------------------------------------------
    def _section(self, title):
        header = tk.Label(self.sidebar, text=title.upper(), font=(UI_FONT, 8, "bold"), anchor="w", padx=16, pady=4)
        header.pack(fill=tk.X, pady=(3, 0))
        frame = tk.Frame(self.sidebar, padx=16, pady=2)
        frame.pack(fill=tk.X)
        self._section_headers.append(header)
        self._frames.append(frame)
        return frame

    def _row(self, parent, label, var, width=10):
        row = tk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        lbl = tk.Label(row, text=label, anchor="w", font=(UI_FONT, 9))
        lbl.pack(side=tk.LEFT)
        ent = tk.Entry(row, textvariable=var, width=width, relief=tk.FLAT, bd=0, highlightthickness=1, font=(self.colors["mono"], 10), justify="right")
        ent.pack(side=tk.RIGHT, ipady=3)
        self._frames.append(row)
        self._labels.append(lbl)
        self._entries.append(ent)
        return ent

    def _button(self, parent, text, command=None, kind="normal", **pack):
        btn = tk.Button(parent, text=text, command=command, relief=tk.FLAT, bd=0, padx=10, pady=6, cursor="hand2", font=(UI_FONT, 9, "bold" if kind != "normal" else "normal"), activeforeground="#ffffff")
        btn.kind = kind
        self._buttons.append(btn)
        if pack:
            btn.pack(**pack)
        return btn

    def _build_sidebar(self):
        self._section_headers, self._frames, self._labels, self._entries, self._buttons = [], [], [], [], []
        s = self.settings
        p = self.profile

        # 연결
        f = self._section("연결")
        self.motor_port_var = tk.StringVar(value=s.motor_port)
        self.indicator_port_var = tk.StringVar(value=s.indicator_port)
        self._row(f, "모터 (Arduino) 포트", self.motor_port_var)
        self._row(f, "로드셀 인디케이터 포트", self.indicator_port_var)
        row = tk.Frame(f); row.pack(fill=tk.X, pady=(8, 0)); self._frames.append(row)
        self.connect_button = self._button(row, "장비 연결", self.connect_devices, kind="accent", side=tk.LEFT, fill=tk.X, expand=True)
        self.disconnect_button = self._button(row, "연결 해제", self.disconnect_devices, side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.disconnect_button.config(state=tk.DISABLED)
        self.save_defaults_button = self._button(f, "현재 설정을 기본값으로 저장", self.save_defaults, fill=tk.X, pady=(6, 0))

        # 수동 제어
        f = self._section(f"수동 제어  ·  키보드 {p.press_key}/{p.release_key}")
        self.jog_speed_var = tk.StringVar(value=s.jog_speed)
        self._row(f, "이동 속도 (mm/s)", self.jog_speed_var)
        row = tk.Frame(f); row.pack(fill=tk.X, pady=(6, 0)); self._frames.append(row)
        self.press_button = self._button(row, p.press_label, kind="warn", side=tk.LEFT, fill=tk.X, expand=True)
        self.release_button = self._button(row, p.release_label, kind="warn", side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        for btn, cmd in ((self.press_button, p.press_cmd), (self.release_button, p.release_cmd)):
            btn.bind("<ButtonPress-1>", lambda _e, c=cmd: self.start_jog(c))
            btn.bind("<ButtonRelease-1>", lambda _e: self.stop_jog())
        hint = tk.Label(f, text="버튼을 누르고 있는 동안 이동하며, 놓으면 정지합니다.", font=(UI_FONT, 8), anchor="w")
        hint.pack(fill=tk.X, pady=(4, 0)); self._labels.append(hint)
        self.zero_button = self._button(f, "⚖  현재 하중을 0점으로", self.zero_force, fill=tk.X, pady=(6, 0))

        # 시험 설정
        f = self._section("시험 설정")
        self.mode_var = tk.StringVar(value="COMPRESSION")
        modes = tk.Frame(f); modes.pack(fill=tk.X); self._frames.append(modes)
        self._radios = []
        for text, value in (("압축 강도  (X: 거리)", "COMPRESSION"), ("Poroelasticity  (X: 시간)", "PORO")):
            rb = tk.Radiobutton(modes, text=text, variable=self.mode_var, value=value, command=self.on_mode_change, font=(UI_FONT, 9), anchor="w", relief=tk.FLAT, bd=0, highlightthickness=0, cursor="hand2")
            rb.pack(fill=tk.X)
            self._radios.append(rb)
        self.distance_var = tk.StringVar(value=s.distance)
        self.speed_var = tk.StringVar(value=s.speed)
        self.filename_var = tk.StringVar(value="Sample_01")
        self.save_dir_var = tk.StringVar(value=s.save_directory)
        self._row(f, "목표 거리 (mm)", self.distance_var)
        self._row(f, "이동 속도 (mm/s)", self.speed_var)
        self.sample_rate_var = tk.StringVar(value=s.sample_rate if s.sample_rate in SAMPLE_RATE_OPTIONS else "10")
        row = tk.Frame(f); row.pack(fill=tk.X, pady=2); self._frames.append(row)
        lbl = tk.Label(row, text="기록 속도 (samples/s, 0=원본)", anchor="w", font=(UI_FONT, 9)); lbl.pack(side=tk.LEFT); self._labels.append(lbl)
        self.sample_rate_box = ttk.Combobox(row, textvariable=self.sample_rate_var, values=list(SAMPLE_RATE_OPTIONS), state="readonly", width=8, style="Hydro.TCombobox", font=(self.colors["mono"], 10), justify="right")
        self.sample_rate_box.pack(side=tk.RIGHT)
        self.sample_rate_box.bind("<<ComboboxSelected>>", lambda _e: self.root.focus_set())
        self._row(f, "파일 이름", self.filename_var, width=16)
        row = tk.Frame(f); row.pack(fill=tk.X, pady=3); self._frames.append(row)
        lbl = tk.Label(row, text="저장 폴더", anchor="w", font=(UI_FONT, 9)); lbl.pack(side=tk.LEFT); self._labels.append(lbl)
        self.dir_button = self._button(row, "폴더…", self.select_directory, side=tk.RIGHT)
        self.dir_label = tk.Label(f, text="", anchor="e", font=(self.colors["mono"], 8))
        self.dir_label.pack(fill=tk.X); self._labels.append(self.dir_label)
        self.save_dir_var.trace_add("write", lambda *_: self._refresh_dir_label())
        self._refresh_dir_label()
        self.distance_var.trace_add("write", lambda *_: self._reset_axes())

        row = tk.Frame(f); row.pack(fill=tk.X, pady=(10, 0)); self._frames.append(row)
        self.start_button = self._button(row, "▶  시험 시작", self.start_test, kind="accent", side=tk.LEFT, fill=tk.X, expand=True)
        self.stop_button = self._button(row, "■  정지 · 기록 종료", self.stop_test, side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        # 안전 / 전원
        f = self._section("안전 · 모터 전원")
        self.estop_button = self._button(f, "⛔  비상 정지  (모터 즉시 정지)", self.emergency_stop, kind="danger", fill=tk.X)
        row = tk.Frame(f); row.pack(fill=tk.X, pady=(6, 0)); self._frames.append(row)
        self.motor_off_button = self._button(row, "⏻  모터 전원 OFF", self.motor_off, side=tk.LEFT, fill=tk.X, expand=True)
        self.motor_on_button = self._button(row, "모터 전원 ON", self.motor_on, side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        hint = tk.Label(f, text="비상 정지(Esc): 이동만 멈추고 위치는 유지.  전원 OFF: 드라이버 차단, 축 자유.", font=(UI_FONT, 8), anchor="w", justify="left", wraplength=316)
        hint.pack(fill=tk.X, pady=(4, 0)); self._labels.append(hint)

    # 메인 -----------------------------------------------------------------
    def _build_main(self):
        top = tk.Frame(self.main, padx=24, pady=16)
        top.pack(fill=tk.X)
        self._main_frames = [top]

        left = tk.Frame(top); left.pack(side=tk.LEFT); self._main_frames.append(left)
        cap = tk.Label(left, text="LIVE FORCE", font=(UI_FONT, 8, "bold"), anchor="w"); cap.pack(anchor="w")
        self.force_label = tk.Label(left, text="0.0000 N", font=(self.colors["mono"], 34, "bold"), anchor="w")
        self.force_label.pack(anchor="w")
        self.grams_label = tk.Label(left, text="0.0 g (raw)", font=(self.colors["mono"], 10), anchor="w")
        self.grams_label.pack(anchor="w")
        self._captions = [cap]

        mid = tk.Frame(top); mid.pack(side=tk.LEFT, padx=40); self._main_frames.append(mid)
        cap2 = tk.Label(mid, text="PEAK", font=(UI_FONT, 8, "bold"), anchor="w"); cap2.pack(anchor="w")
        self.peak_label = tk.Label(mid, text="0.0000 N", font=(self.colors["mono"], 18), anchor="w"); self.peak_label.pack(anchor="w")
        cap3 = tk.Label(mid, text="DISTANCE / TIME", font=(UI_FONT, 8, "bold"), anchor="w"); cap3.pack(anchor="w", pady=(6, 0))
        self.progress_label = tk.Label(mid, text="0.00 mm  ·  0.0 s", font=(self.colors["mono"], 12), anchor="w"); self.progress_label.pack(anchor="w")
        self._captions += [cap2, cap3]

        right = tk.Frame(top); right.pack(side=tk.RIGHT); self._main_frames.append(right)
        self.state_badge = tk.Label(right, text="  READY  ", font=(UI_FONT, 10, "bold"), padx=14, pady=8)
        self.state_badge.pack(anchor="e")
        self.csv_label = tk.Label(right, text="", font=(self.colors["mono"], 8), anchor="e")
        self.csv_label.pack(anchor="e", pady=(6, 0))

        graph_host = tk.Frame(self.main, padx=16, pady=8)
        graph_host.pack(fill=tk.BOTH, expand=True)
        self._main_frames.append(graph_host)
        self.figure = Figure(figsize=(7, 5), dpi=100)
        self.axes = self.figure.add_subplot(111)
        (self.line,) = self.axes.plot([], [], linewidth=1.8)
        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_host)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.get_tk_widget().configure(highlightthickness=0)

    def _bind_keys(self):
        p = self.profile
        self.root.bind(f"<KeyPress-{p.press_key}>", lambda e: self._on_key_press(p.press_cmd, e))
        self.root.bind(f"<KeyRelease-{p.press_key}>", self._on_key_release)
        self.root.bind(f"<KeyPress-{p.release_key}>", lambda e: self._on_key_press(p.release_cmd, e))
        self.root.bind(f"<KeyRelease-{p.release_key}>", self._on_key_release)
        self.root.bind("<Escape>", lambda _e: self.emergency_stop())
        self.root.bind("<Button-1>", self._clear_focus)
        for ent in self._entries:
            ent.bind("<Return>", lambda _e: self.root.focus_set())

    def _clear_focus(self, event):
        if not isinstance(event.widget, tk.Entry):
            self.root.focus_set()

    # ------------------------------------------------------------------ 테마
    def _apply_theme(self):
        c = self.colors
        self.root.configure(bg=c["bg"])
        self.body.configure(bg=c["bg"])
        self.titlebar.configure(bg=c["activity"])
        self.title_label.configure(bg=c["activity"], fg="#ffffff")
        self.subtitle_label.configure(bg=c["activity"], fg="#bbbbbb")
        self.theme_button.configure(bg=c["activity"], fg="#dddddd", activebackground=c["button_hover"], text="☀  밝은 테마" if c["name"] == "dark" else "☾  어두운 테마")
        self.update_button.configure(bg=c["activity"], fg="#dddddd", activebackground=c["button_hover"])
        self.style.configure("Hydro.TCombobox", fieldbackground=c["input"], background=c["input"], foreground=c["text_strong"], arrowcolor=c["text"], bordercolor=c["input_border"], selectbackground=c["input"], selectforeground=c["text_strong"], padding=2)
        self.style.map("Hydro.TCombobox", fieldbackground=[("readonly", c["input"])], selectbackground=[("readonly", c["input"])], selectforeground=[("readonly", c["text_strong"])])
        self.root.option_add("*TCombobox*Listbox.background", c["input"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text_strong"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.statusbar.configure(bg=c["status"])
        for lbl in (self.sb_device, self.sb_conn, self.sb_motor, self.sb_message, self.sb_clock, self.sb_samples):
            lbl.configure(bg=c["status"], fg=c["status_text"])
        self.sidebar.configure(bg=c["sidebar"])
        self.sidebar_sep.configure(bg=c["border"])
        for h in self._section_headers:
            h.configure(bg=c["sidebar"], fg=c["muted"])
        for fr in self._frames:
            fr.configure(bg=c["sidebar"])
        for lbl in self._labels:
            lbl.configure(bg=c["sidebar"], fg=c["text"])
        for ent in self._entries:
            ent.configure(bg=c["input"], fg=c["text_strong"], insertbackground=c["text_strong"], highlightbackground=c["input_border"], highlightcolor=c["focus"], disabledbackground=c["sidebar"])
        for rb in self._radios:
            rb.configure(bg=c["sidebar"], fg=c["text"], activebackground=c["sidebar"], activeforeground=c["text_strong"], selectcolor=c["input"])
        for btn in self._buttons:
            self._style_button(btn)
        self.main.configure(bg=c["bg"])
        for fr in self._main_frames:
            fr.configure(bg=c["bg"])
        for cap in self._captions:
            cap.configure(bg=c["bg"], fg=c["muted"])
        self.force_label.configure(bg=c["bg"], fg=c["text_strong"])
        self.grams_label.configure(bg=c["bg"], fg=c["muted"])
        self.peak_label.configure(bg=c["bg"], fg=c["text"])
        self.progress_label.configure(bg=c["bg"], fg=c["text"])
        self.csv_label.configure(bg=c["bg"], fg=c["muted"])
        self._set_state(self._state if hasattr(self, "_state") else "READY")
        self._style_graph()
        self._refresh_motor_status()

    def _style_button(self, btn):
        c = self.colors
        kind = getattr(btn, "kind", "normal")
        palette = {
            "normal": (c["button"], c["button_hover"], c["text_strong"]),
            "accent": (c["accent"], c["accent_hover"], "#ffffff"),
            "danger": (c["danger"], c["danger_hover"], "#ffffff"),
            "warn": (c["warn"], c["warn_hover"], "#ffffff"),
        }[kind]
        btn.configure(bg=palette[0], activebackground=palette[1], fg=palette[2], disabledforeground=c["muted"])
        btn.bind("<Enter>", lambda _e, b=btn, col=palette[1]: b["state"] == tk.NORMAL and b.configure(bg=col))
        btn.bind("<Leave>", lambda _e, b=btn, col=palette[0]: b.configure(bg=col))

    def _style_graph(self):
        c = self.colors
        self.figure.set_facecolor(c["bg"])
        self.axes.set_facecolor(c["bg"])
        self.axes.tick_params(colors=c["muted"], labelsize=9)
        for spine in self.axes.spines.values():
            spine.set_color(c["border"])
        self.axes.grid(True, color=c["graph_grid"], linewidth=0.8, alpha=0.9)
        self.axes.set_ylabel("Force (N)", color=c["muted"])
        self.line.set_color(c["graph_line"])
        self.canvas.get_tk_widget().configure(bg=c["bg"])
        self._reset_axes()

    def _reset_axes(self):
        c = self.colors
        if self.is_running:
            return
        if self.mode_var.get() == "COMPRESSION":
            try:
                d = float(self.distance_var.get())
            except ValueError:
                d = 5.0
            self.axes.set_xlim(0, d * 1.05 if d > 0 else 5.0)
            self.axes.set_xlabel("Distance (mm)", color=c["muted"])
            self.axes.set_title("Compressive strength  ·  Force vs Distance", color=c["text"], loc="left", fontsize=11, pad=12)
        else:
            self.axes.set_xlim(0, 60)
            self.axes.set_xlabel("Time (s)", color=c["muted"])
            self.axes.set_title("Poroelasticity / Stress relaxation  ·  Force vs Time", color=c["text"], loc="left", fontsize=11, pad=12)
        self.axes.set_ylim(-0.01, 0.2)
        self.canvas.draw_idle()

    def toggle_theme(self):
        self.colors = THEMES["light" if self.colors["name"] == "dark" else "dark"]
        self._apply_theme()
        self._save_settings(quiet=True)

    # ------------------------------------------------------------------ 상태 표시
    def _set_state(self, state):
        c = self.colors
        self._state = state
        color = {
            "READY": c["muted"], "RUNNING": c["blue"], "HOLDING": c["yellow"], "DONE": c["green"],
            "STOPPED": c["yellow"], "E-STOP": c["red"], "MOTOR OFF": c["red"], "JOG": c["yellow"],
        }.get(state, c["muted"])
        self.state_badge.configure(text=f"  {state}  ", bg=color, fg="#ffffff" if c["name"] == "dark" or state in ("E-STOP", "MOTOR OFF") else "#000000")

    def _message(self, text, level="info"):
        c = self.colors
        icon = {"info": "ℹ", "ok": "✓", "warn": "⚠", "error": "✖"}[level]
        self.sb_message.configure(text=f"{icon}  {text}")
        self.sb_clock.configure(text=datetime.now().strftime("%H:%M:%S"))
        if level == "error":
            self.sb_message.configure(fg="#ffd7d7")
        elif level == "warn":
            self.sb_message.configure(fg="#ffe9a8")
        else:
            self.sb_message.configure(fg=c["status_text"])

    def _refresh_motor_status(self):
        self.sb_motor.configure(text="⏻ 모터 전원 ON" if self.motor_enabled else "⏻ 모터 전원 OFF")

    def _notify(self, kind, title, text):
        if self.show_dialogs:
            getattr(messagebox, kind)(title, text, parent=self.root)

    # ------------------------------------------------------------------ 설정
    def _save_settings(self, quiet=False):
        self.settings = AppSettings(
            motor_port=self.motor_port_var.get().strip().upper(),
            indicator_port=self.indicator_port_var.get().strip().upper(),
            save_directory=self.save_dir_var.get().strip(),
            theme=self.colors["name"],
            jog_speed=self.jog_speed_var.get().strip(),
            distance=self.distance_var.get().strip(),
            speed=self.speed_var.get().strip(),
            sample_rate=self.sample_rate_var.get().strip(),
        )
        path = save_settings(self.settings, self.config_path)
        if not quiet:
            self._message(f"기본값 저장됨: {path}", "ok")
        return path

    def save_defaults(self):
        try:
            path = self._save_settings()
            self._notify("showinfo", "기본값 저장", f"설정을 저장했습니다.\n{path}")
        except OSError as exc:
            self._notify("showerror", "저장 오류", str(exc))

    def _refresh_dir_label(self):
        path = self.save_dir_var.get().strip() or "(프로그램 실행 폴더)"
        self.dir_label.configure(text=path if len(path) <= 44 else "…" + path[-43:])

    def select_directory(self):
        selected = filedialog.askdirectory(title="데이터 저장 폴더 선택", parent=self.root)
        if selected:
            self.save_dir_var.set(selected)

    # ------------------------------------------------------------------ 연결
    def connect_devices(self):
        try:
            if self.demo:
                self.demo_rig = DemoRig(self.profile.press_cmd, self.profile.release_cmd)
                self.arduino, self.indicator = self.demo_rig.motor, self.demo_rig.indicator
            else:
                self.arduino = self.serial_factory(self.motor_port_var.get().strip().upper(), MOTOR_BAUDRATE, timeout=0.1)
                self.indicator = self.serial_factory(self.indicator_port_var.get().strip().upper(), INDICATOR_BAUDRATE, timeout=0.1)
        except Exception as exc:
            self.disconnect_devices()
            self._message(f"연결 실패: {exc}", "error")
            self._notify("showerror", "연결 오류", str(exc))
            return
        threading.Thread(target=self._reader, args=(self.arduino, "motor"), daemon=True).start()
        threading.Thread(target=self._reader, args=(self.indicator, "loadcell"), daemon=True).start()
        self.motor_enabled = True
        self._refresh_motor_status()
        self.connect_button.config(state=tk.DISABLED)
        self.disconnect_button.config(state=tk.NORMAL)
        self.sb_conn.configure(text=("● DEMO" if self.demo else f"● {self.motor_port_var.get().upper()} · {self.indicator_port_var.get().upper()}"))
        self._message("장비 연결됨. 조그로 접촉 위치를 맞춘 뒤 0점을 잡고 시험을 시작하세요.", "ok")

    def disconnect_devices(self):
        if self.is_running:
            self.stop_test()
        if self._motor_ready():
            try:
                self.arduino.write(stop_command())
                time.sleep(0.05)
            except Exception:
                pass
        if self.demo_rig:
            self.demo_rig.close()
            self.demo_rig = None
        for dev in (self.arduino, self.indicator):
            if dev and getattr(dev, "is_open", False):
                try:
                    dev.close()
                except Exception:
                    pass
        self.arduino = self.indicator = None
        self.connect_button.config(state=tk.NORMAL)
        self.disconnect_button.config(state=tk.DISABLED)
        self.sb_conn.configure(text="○ 연결 안 됨")
        self._set_state("READY")
        self._message("연결 해제됨")

    def _motor_ready(self):
        return bool(self.arduino and getattr(self.arduino, "is_open", False))

    def _reader(self, device, tag):
        while device and getattr(device, "is_open", False) and not self.closing:
            try:
                line = device.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                break
            if line:
                self.rx_queue.put((tag, line))

    def _pump_queue(self):
        if self.closing:
            return
        while True:
            try:
                tag, line = self.rx_queue.get_nowait()
            except queue.Empty:
                break
            if tag == "loadcell":
                self._on_loadcell_line(line)
            else:
                self._on_motor_line(line)
        self._check_move_timeout()
        self._schedule(30, self._pump_queue)

    # ------------------------------------------------------------------ 수신 처리
    def _on_motor_line(self, line):
        if line == "DONE":
            self._on_move_done()
        elif line == "STOP_BUTTON":
            self._finish_test("STOPPED")
            self._message("하드웨어 비상 정지 버튼이 눌렸습니다.", "warn")
        elif line == "MOTOR_OFF":
            self.motor_enabled = False
            self._refresh_motor_status()
        elif line == "MOTOR_ON":
            self.motor_enabled = True
            self._refresh_motor_status()

    def _on_loadcell_line(self, line):
        grams = parse_indicator_grams(line)
        if grams is None:
            return
        # 인디케이터는 압축 시 음수를 보낸다 → 압축력을 양수 N으로 표시
        self.current_grams = grams
        force = -grams_to_newton(grams) - self.force_offset_N
        self.force_label.configure(text=f"{force:.4f} N")
        self.grams_label.configure(text=f"{grams:.1f} g (raw)")
        if abs(grams) > self.profile.max_load_g * 0.9:
            self._message(f"하중이 로드셀 용량({self.profile.loadcell})의 90%를 넘었습니다!", "error")
        if self.is_running:
            self._accumulate(force)

    # ------------------------------------------------------------------ 기록 속도(평균)
    def _sample_interval(self):
        """기록 간격(초). 0이면 원본 전부 기록."""
        try:
            rate = float(self.sample_rate_var.get())
        except (ValueError, AttributeError):
            rate = 10.0
        return 1.0 / rate if rate > 0 else 0.0

    def _accumulate(self, force):
        interval = self._sample_interval()
        if interval <= 0:
            self._record(force)
            return
        now = time.time()
        self._bucket.append(force)
        if now - self._bucket_t0 >= interval:
            self._record(sum(self._bucket) / len(self._bucket))
            self._bucket = []
            self._bucket_t0 += interval * int((now - self._bucket_t0) / interval)

    def _flush_bucket(self):
        if self._bucket:
            self._record(sum(self._bucket) / len(self._bucket))
            self._bucket = []

    # ------------------------------------------------------------------ GitHub 업데이트
    def check_for_update(self):
        self.update_button.configure(state=tk.DISABLED, text="⬇  확인 중…")
        self._message("GitHub에서 최신 버전을 확인하는 중…")
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self):
        try:
            info = updater.fetch_latest()
        except Exception as exc:
            self.root.after(0, lambda: self._update_failed(f"버전 확인 실패: {exc}"))
            return
        self.root.after(0, lambda: self._update_checked(info))

    def _update_failed(self, msg):
        self.update_button.configure(state=tk.NORMAL, text=f"⬇  업데이트 확인  (v{__version__})")
        self._message(msg, "error")
        self._notify("showerror", "업데이트", msg + f"\n\n수동 다운로드: {updater.RELEASES_PAGE}")

    def _update_checked(self, info):
        self._update_info = info
        self.update_button.configure(state=tk.NORMAL, text=f"⬇  업데이트 확인  (v{__version__})")
        asset_name = f"HydrogelStudio_{self.profile.key}.exe"
        asset = updater.find_asset(info, asset_name)
        if not updater.is_newer(info["tag"], __version__):
            self._message(f"최신 버전입니다 (v{__version__}). GitHub 최신: {info['tag'] or '없음'}", "ok")
            self._notify("showinfo", "업데이트", f"현재 v{__version__} 이 최신입니다.")
            return
        if asset is None:
            self._message(f"새 버전 {info['tag']} 이 있지만 {asset_name} 파일이 첨부되지 않았습니다.", "warn")
            self._notify("showwarning", "업데이트", f"새 버전 {info['tag']} 릴리스에 {asset_name} 이 없습니다.\n{info['html_url']}")
            return
        size_mb = asset["size"] / 1e6
        notes = (info["notes"] or "").strip()
        notes = (notes[:600] + "…") if len(notes) > 600 else notes
        if not self.show_dialogs or messagebox.askyesno(
            "새 버전 발견",
            f"현재 v{__version__} → 새 버전 {info['tag']}\n파일: {asset_name} ({size_mb:.0f} MB)\n\n{notes}\n\n지금 다운로드할까요?",
            parent=self.root,
        ):
            self._start_download(asset)

    def _start_download(self, asset):
        exe = updater.running_exe()
        folder = exe.parent if exe else Path.cwd()
        dest = folder / f"HydrogelStudio_{self.profile.key}_{self._update_info['tag']}.exe"
        self.update_button.configure(state=tk.DISABLED, text="⬇  다운로드 중…")

        def progress(got, total):
            pct = got / total * 100 if total else 0
            self.root.after(0, lambda: self.update_button.configure(text=f"⬇  다운로드 {pct:3.0f} %"))

        def worker():
            try:
                path = updater.download(asset["url"], dest, progress)
            except Exception as exc:
                self.root.after(0, lambda: self._update_failed(f"다운로드 실패: {exc}"))
                return
            self.root.after(0, lambda: self._download_done(path, exe))

        threading.Thread(target=worker, daemon=True).start()

    def _download_done(self, path, exe):
        self.update_button.configure(state=tk.NORMAL, text=f"⬇  업데이트 확인  (v{__version__})")
        self._message(f"다운로드 완료: {path}", "ok")
        if exe and self.show_dialogs and messagebox.askyesno(
            "업데이트 설치",
            f"다운로드했습니다:\n{path}\n\n지금 프로그램을 닫고 새 버전으로 교체한 뒤 다시 실행할까요?\n(기존 파일은 .bak 으로 남습니다. 시험 중이면 '아니오'를 누르세요.)",
            parent=self.root,
        ):
            updater.replace_and_restart(path, exe)
            self.close()
        elif not exe:
            self._notify("showinfo", "업데이트", f"새 exe를 저장했습니다:\n{path}\n(개발 환경에서는 자동 교체를 하지 않습니다.)")

    def _record(self, force):
        elapsed = time.time() - self.start_time
        if self.motor_moving:
            distance = min(elapsed * self.test_speed, self.target_dist)
        else:
            distance = self.target_dist
        self.time_data.append(elapsed)
        self.dist_data.append(distance)
        self.force_data.append(force)
        self.peak_force_N = max(self.peak_force_N, force)
        self.peak_label.configure(text=f"{self.peak_force_N:.4f} N")
        self.progress_label.configure(text=f"{distance:.2f} mm  ·  {elapsed:.1f} s")
        self.sb_samples.configure(text=f"{len(self.time_data)} samples · {self.sample_rate_var.get()}/s")
        if self.csv_writer:
            self.csv_writer.writerow([round(elapsed, 3), round(distance, 4), round(force, 4)])

    def _check_move_timeout(self):
        """DONE 신호를 놓쳤을 때를 대비한 보조 판정 (예상 시간의 1.5배 + 2초)."""
        if self.is_running and self.motor_moving:
            if time.time() - self.start_time > self.expected_move_s * 1.5 + 2.0:
                self._message("완료 신호(DONE)를 받지 못해 시간 기준으로 이동 완료 처리했습니다.", "warn")
                self._on_move_done()

    def _on_move_done(self):
        if not self.motor_moving:
            return
        self.motor_moving = False
        if not self.is_running:
            return
        if self.mode_var.get() == "COMPRESSION":
            self._finish_test("DONE")
            self._message(f"압축 시험 완료. 최대 하중 {self.peak_force_N:.4f} N", "ok")
        else:
            self._set_state("HOLDING")
            self._message("목표 거리 도달. 변위를 유지하며 하중을 계속 기록합니다. 끝내려면 '정지 · 기록 종료'를 누르세요.", "info")

    # ------------------------------------------------------------------ 조그
    def _on_key_press(self, cmd, event):
        if isinstance(event.widget, tk.Entry):
            return
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
            self.key_release_timer = None
        if self.jog_key_active != cmd:
            self.jog_key_active = cmd
            self.start_jog(cmd)

    def _on_key_release(self, event):
        if isinstance(event.widget, tk.Entry):
            return
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
        self.key_release_timer = self.root.after(60, self._key_release_now)

    def _key_release_now(self):
        self.key_release_timer = None
        self.jog_key_active = None
        self.stop_jog()

    def start_jog(self, cmd):
        if not self._motor_ready():
            self._message("장비가 연결되어 있지 않습니다.", "warn")
            return
        if self.is_running:
            self._message("시험 중에는 조그를 사용할 수 없습니다.", "warn")
            return
        try:
            self.arduino.write(jog_command(cmd, self.jog_speed_var.get()))
        except ValueError as exc:
            self._message(str(exc), "error")
            return
        self.jogging = True
        self.motor_enabled = True
        self._refresh_motor_status()
        self._set_state("JOG")

    def stop_jog(self):
        if not self.jogging:
            return
        self.jogging = False
        if self._motor_ready():
            self.arduino.write(stop_command())
        self._set_state("READY" if not self.is_running else self._state)

    def zero_force(self):
        self.force_offset_N = -grams_to_newton(self.current_grams)
        self.force_label.configure(text="0.0000 N")
        self._message(f"0점 설정 (raw {self.current_grams:.1f} g)", "ok")

    # ------------------------------------------------------------------ 시험
    def start_test(self):
        if not self._motor_ready():
            self._message("장비를 먼저 연결하세요.", "warn")
            self._notify("showwarning", "연결 필요", "장비를 먼저 연결하세요.")
            return
        if self.is_running:
            return
        try:
            command = start_command(self.distance_var.get(), self.speed_var.get())
            self.target_dist = float(self.distance_var.get())
            self.test_speed = float(self.speed_var.get())
            self._open_csv()
        except (OSError, ValueError) as exc:
            self._message(str(exc), "error")
            self._notify("showerror", "시작 오류", str(exc))
            return
        self.time_data.clear(); self.dist_data.clear(); self.force_data.clear()
        self.line.set_data([], [])
        self.peak_force_N = 0.0
        self._reset_axes()
        self.expected_move_s = self.target_dist / self.test_speed
        self.start_time = time.time()
        self._bucket = []
        self._bucket_t0 = self.start_time
        self.motor_moving = True
        self.is_running = True
        self.motor_enabled = True
        self._refresh_motor_status()
        self.arduino.write(command)
        self._set_state("RUNNING")
        self._message(f"시험 시작: {self.target_dist:g} mm @ {self.test_speed:g} mm/s (예상 {self.expected_move_s:.1f} s)", "ok")
        self._save_settings(quiet=True)

    def _open_csv(self):
        self._close_csv()
        base = self.filename_var.get().strip() or ("Compress" if self.mode_var.get() == "COMPRESSION" else "Poroelasticity")
        name = f"{base}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        save_dir = self.save_dir_var.get().strip()
        path = Path(save_dir) / name if save_dir else Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path = path
        self.csv_file = path.open("w", newline="", encoding="utf-8-sig")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(["Time (s)", "Distance (mm)", "Force (N)"])
        self.csv_label.configure(text=f"기록 중: {path.name}")

    def _close_csv(self):
        if self.csv_file and not self.csv_file.closed:
            self.csv_file.close()
        self.csv_file = self.csv_writer = None

    def _finish_test(self, state):
        was_running = self.is_running
        if was_running:
            self._flush_bucket()
        self.is_running = False
        self.motor_moving = False
        self._close_csv()
        self._set_state(state)
        if was_running and self.csv_path:
            self.csv_label.configure(text=f"저장됨: {self.csv_path.name}")

    def stop_test(self):
        if self._motor_ready():
            self.arduino.write(stop_command())
        if self.is_running:
            self._finish_test("STOPPED")
            self._message(f"기록 종료. {len(self.time_data)} 샘플 저장.", "ok")
        else:
            self._set_state("READY")

    def emergency_stop(self):
        self.jogging = False
        self.jog_key_active = None
        if self._motor_ready():
            self.arduino.write(stop_command())
        self._finish_test("E-STOP")
        self._message("비상 정지: 모터 이동을 멈췄습니다. 드라이버는 현재 위치를 유지합니다.", "warn")

    def motor_off(self):
        if not self._motor_ready():
            self._message("장비가 연결되어 있지 않습니다.", "warn")
            return
        self.jogging = False
        self.arduino.write(motor_off_command())
        self.motor_enabled = False
        self._refresh_motor_status()
        self._finish_test("MOTOR OFF")
        self._message("모터 전원 OFF: 드라이버 전원을 차단했습니다. 축이 자유롭게 움직일 수 있습니다.", "warn")

    def motor_on(self):
        if not self._motor_ready():
            self._message("장비가 연결되어 있지 않습니다.", "warn")
            return
        self.arduino.write(motor_on_command())
        self.motor_enabled = True
        self._refresh_motor_status()
        self._set_state("READY")
        self._message("모터 전원 ON", "ok")

    def on_mode_change(self):
        if self.is_running:
            return
        self.time_data.clear(); self.dist_data.clear(); self.force_data.clear()
        self.line.set_data([], [])
        self._reset_axes()

    # ------------------------------------------------------------------ 그래프
    def _update_graph(self):
        if self.closing:
            return
        n = len(self.force_data)
        if self.is_running and n:
            xs = self.dist_data if self.mode_var.get() == "COMPRESSION" else self.time_data
            self.line.set_data(xs[:n], self.force_data[:n])
            xmax = self.axes.get_xlim()[1]
            if xs[-1] > xmax:
                self.axes.set_xlim(0, xs[-1] * 1.25)
            lo, hi = min(self.force_data), max(self.force_data)
            ylo, yhi = self.axes.get_ylim()
            if hi > yhi or lo < ylo:
                span = max(hi - lo, 0.05)
                self.axes.set_ylim(min(lo, -0.01) - span * 0.1, hi + span * 0.25)
            self.canvas.draw_idle()
        self._schedule(80, self._update_graph)

    # ------------------------------------------------------------------ 종료
    def close(self):
        self.closing = True
        for aid in self.after_ids:
            try:
                self.root.after_cancel(aid)
            except tk.TclError:
                pass
        if self.is_running:
            self._finish_test("STOPPED")
        self.disconnect_devices()
        self.root.destroy()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=PROFILE.title)
    parser.add_argument("--demo", action="store_true", help="하드웨어 없이 가상 장비로 실행")
    parser.add_argument("--theme", choices=("dark", "light"), default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = tk.Tk()
    HydrogelStudio(root, PROFILE, demo=args.demo, initial_theme=args.theme)
    root.mainloop()
