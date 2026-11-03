"""Hydrogel Compression Studio v3 - Tkinter GUI (VS Code 스타일)."""
import argparse
import csv
import math
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
from .layout import fit_window, work_area
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
from .step_log import StepLog
from .theme import THEMES
from . import updater
from .motion import move_duration, position_at

MOTOR_BAUDRATE = 115200
INDICATOR_BAUDRATE = 115200
UI_FONT = "Segoe UI"
SAMPLE_RATE_OPTIONS = ("0", "10", "5", "2", "1", "0.5", "0.2")   # samples/s, 0 = 원본 전부
MODULUS_CAPTION = "COMPRESSIVE MODULUS  (linear fit, 5-15 % strain)"
MODULUS_CAPTION_COMPACT = "MODULUS (5-15 %)"
CSV_LABEL_PACK = {"fill": tk.X, "pady": (6, 0)}
START_POINT_SPEED = 0.01       # mm/s, 탐색 속도 고정
START_POINT_TIMEOUT = 2.0      # 유효 하중 수신 / 정지 응답 대기 상한(초)


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
        self._starting_point = None   # None / seeking / stopping / zeroing
        self._start_phase_at = 0.0
        self._last_loadcell_at = 0.0
        self._start_force_N = 0.01
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
        self.strain_data, self.stress_data = [], []     # 변형률(-), 응력(kPa)
        self.area_mm2 = 0.0
        self.height0_mm = 0.0
        self.modulus_kPa = None
        self._running_mode = "COMPRESSION"
        self.rx_queue = queue.Queue()
        self.after_ids = []
        self.closing = False
        self.key_release_timer = None
        self.jog_key_active = None
        self._bucket = []            # 평균용 힘 버퍼
        self._bucket_t0 = 0.0        # 현재 버킷 시작 시각
        self._update_info = None
        self._live_distance = 0.0     # 마지막으로 계산한 명령 위치(mm), 시험 후 표시용
        self._wheel_acc = 0.0         # 한 칸보다 작은 휠 값을 모으는 누적기(px)
        self._compact = False         # 메인 상단 지표 축소 표시 여부
        self._step_log = None
        self._step_log_phase = None
        self._step_log_phase_at = 0.0
        self.step_log_path = None

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
        w, h, x, y, min_w, min_h = fit_window(work_area(self.root))
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min_w, min_h)

        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.style.layout("Hydro.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])

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
    def _section(self, title, parent=None):
        parent = parent or self.sidebar_inner
        header = tk.Label(parent, text=title.upper(), font=(UI_FONT, 8, "bold"), anchor="w", padx=16, pady=4)
        header.pack(fill=tk.X, pady=(3, 0))
        frame = tk.Frame(parent, padx=16, pady=2)
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

        # 고정 영역(시험 시작·정지·비상 정지)을 먼저 배치해 창이 낮아도 공간을 먼저 차지하게 한다. 나머지는 스크롤 영역.
        self.sidebar_footer = tk.Frame(self.sidebar)
        self.sidebar_footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.footer_sep = tk.Frame(self.sidebar, height=1)
        self.footer_sep.pack(side=tk.BOTTOM, fill=tk.X)
        self.sidebar_host = tk.Frame(self.sidebar)
        self.sidebar_host.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.sidebar_footer.lift()      # Tab 이동은 쌓임 순서를 따른다. 고정 영역을 스크롤 영역 뒤로 두어 기존 Tab 순서를 유지한다
        # yscrollincrement는 1이어야 한다. 더 크면 Tk가 증분 단위로만 움직여 내용 맨 아래 몇 픽셀에 닿지 못한다
        self.sidebar_canvas = tk.Canvas(self.sidebar_host, highlightthickness=0, bd=0, yscrollincrement=1)
        self.sidebar_canvas.pack(fill=tk.BOTH, expand=True)
        self.sidebar_inner = tk.Frame(self.sidebar_canvas)
        self._sidebar_window = self.sidebar_canvas.create_window(0, 0, window=self.sidebar_inner, anchor="nw")
        self.sidebar_scroll = ttk.Scrollbar(self.sidebar_host, orient=tk.VERTICAL, command=self.sidebar_canvas.yview, style="Hydro.Vertical.TScrollbar")
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scroll.set)
        self.sidebar_canvas.bind("<Configure>", self._on_sidebar_resize)
        self.sidebar_inner.bind("<Configure>", self._on_sidebar_resize)

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
        row = tk.Frame(f); row.pack(fill=tk.X, pady=(6, 0)); self._frames.append(row)
        self.save_defaults_button = self._button(row, "현재 설정을 기본값으로 저장", self.save_defaults, side=tk.LEFT, fill=tk.X, expand=True)
        self.step_log_button = self._button(row, "STEP 로그", self.toggle_step_log, side=tk.RIGHT, padx=(6, 0))

        # 수동 제어
        f = self._section(f"수동 제어  ·  키보드 {p.press_key}/{p.release_key}")
        self.jog_speed_var = tk.StringVar(value=s.jog_speed)
        self._row(f, "이동 속도 (mm/s)", self.jog_speed_var)
        row = tk.Frame(f); row.pack(fill=tk.X, pady=(6, 0)); self._frames.append(row)
        self.press_button = self._button(row, p.press_label, kind="warn", side=tk.LEFT, fill=tk.X, expand=True)  # 누르고 있는 동안 이동
        self.release_button = self._button(row, p.release_label, kind="warn", side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        for btn, cmd in ((self.press_button, p.press_cmd), (self.release_button, p.release_cmd)):
            btn.bind("<ButtonPress-1>", lambda _e, c=cmd: self.start_jog(c))
            btn.bind("<ButtonRelease-1>", lambda _e: self.stop_jog())
        self.zero_button = self._button(f, "⚖  현재 하중을 0점으로", self.zero_force, fill=tk.X, pady=(6, 0))
        self.start_force_var = tk.StringVar(value=s.start_force)
        self.start_force_entry = self._row(f, "Starting point 하중 (N)", self.start_force_var)
        self.start_point_button = self._button(f, "Starting point 찾기 · 0.01 mm/s", self.find_start_point, fill=tk.X, pady=(4, 0))

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
        self.eta_label = tk.Label(f, text="", anchor="e", font=(self.colors["mono"], 9))
        self.eta_label.pack(fill=tk.X); self._labels.append(self.eta_label)
        for var in (self.distance_var, self.speed_var):
            var.trace_add("write", lambda *_: self._refresh_eta())
        self.diameter_var = tk.StringVar(value=s.diameter)
        self.height_var = tk.StringVar(value=s.height)
        self._row(f, "시편 직경 (mm, 원통형)", self.diameter_var)
        self._row(f, "시편 초기 높이 (mm)", self.height_var)
        self.plot_var = tk.StringVar(value="STRESS")
        plots = tk.Frame(f); plots.pack(fill=tk.X, pady=(2, 0)); self._frames.append(plots)
        lbl = tk.Label(plots, text="그래프", anchor="w", font=(UI_FONT, 9)); lbl.pack(side=tk.LEFT); self._labels.append(lbl)
        self.plot_radios = {}
        for value in ("FORCE", "STRESS"):
            rb = tk.Radiobutton(plots, text="", variable=self.plot_var, value=value, command=self._on_plot_change, font=(UI_FONT, 9), relief=tk.FLAT, bd=0, highlightthickness=0, cursor="hand2")
            rb.pack(side=tk.RIGHT, padx=(8, 0))
            self._radios.append(rb)
            self.plot_radios[value] = rb
        self._refresh_plot_labels()
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
        for var in (self.distance_var, self.diameter_var, self.height_var):
            var.trace_add("write", lambda *_: self._reset_axes())

        # 여기부터 고정 영역: 작은 화면에서도 비상 정지와 기록 종료를 찾아 스크롤할 일이 없어야 한다
        f = tk.Frame(self.sidebar_footer, padx=16, pady=2); f.pack(fill=tk.X); self._frames.append(f)
        row = tk.Frame(f); row.pack(fill=tk.X, pady=(10, 0)); self._frames.append(row)
        self.start_button = self._button(row, "▶  시험 시작", self.start_test, kind="accent", side=tk.LEFT, fill=tk.X, expand=True)
        self.stop_button = self._button(row, "■  정지 · 기록 종료", self.stop_test, side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        # 안전 / 전원
        f = self._section("안전 · 모터 전원", parent=self.sidebar_footer)
        self.estop_button = self._button(f, "⛔  비상 정지  (모터 즉시 정지)", self.emergency_stop, kind="danger", fill=tk.X)
        row = tk.Frame(f); row.pack(fill=tk.X, pady=(6, 0)); self._frames.append(row)
        self.motor_off_button = self._button(row, "⏻  모터 전원 OFF", self.motor_off, side=tk.LEFT, fill=tk.X, expand=True)
        self.motor_on_button = self._button(row, "모터 전원 ON", self.motor_on, side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        hint = tk.Label(f, text="Esc = 비상 정지(위치 유지)  ·  전원 OFF = 드라이버 차단, 축 자유", font=(UI_FONT, 8), anchor="w")
        hint.pack(fill=tk.X, pady=(3, 0)); self._labels.append(hint)

        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        # 두 위젯은 클래스 바인딩이 휠을 따로 처리한다 (콤보박스는 기록 속도 값이 바뀌고, 스크롤바는 두 번 움직인다)
        self.sample_rate_box.bind("<MouseWheel>", self._on_mousewheel_break)
        self.sidebar_scroll.bind("<MouseWheel>", self._on_mousewheel_break)
        self._bind_focus_scroll(self.sidebar_inner)

    # 사이드바 스크롤 ---------------------------------------------------------
    def _on_sidebar_resize(self, _event=None):
        canvas = self.sidebar_canvas
        width = canvas.winfo_width()
        content_h = self.sidebar_inner.winfo_reqheight()
        canvas.itemconfigure(self._sidebar_window, width=width)
        canvas.configure(scrollregion=(0, 0, width, content_h))
        if content_h > canvas.winfo_height():
            # 오른쪽 여백(16 px) 위에 겹쳐 놓으므로 내용 폭이 변하지 않아 나타났다 사라졌다 반복하지 않는다
            self.sidebar_scroll.place(relx=1.0, rely=0, relheight=1.0, width=10, anchor="ne")
            self.sidebar_scroll.lift()
        else:
            self.sidebar_scroll.place_forget()
            canvas.yview_moveto(0)

    def _on_mousewheel(self, event):
        # 조그 버튼을 누른 채 스크롤하면 버튼이 포인터에서 벗어날 수 있다
        if self.closing or self.jogging:
            return
        # 열린 콤보박스 목록은 스스로 스크롤한다. event.widget이 문자열일 수 있으므로 경로 글자로만 판단한다
        if ".popdown" in str(event.widget):
            return
        canvas = self.sidebar_canvas
        x, y = event.x_root - canvas.winfo_rootx(), event.y_root - canvas.winfo_rooty()
        if not (0 <= x < canvas.winfo_width() and 0 <= y < canvas.winfo_height()):
            return
        if self.sidebar_inner.winfo_reqheight() <= canvas.winfo_height():
            return
        # 휠 한 칸(120) = 60 px. 터치패드의 작은 값은 현재 Tk(8.6.12)가 120 단위로 모아서 넘겨 주지만,
        # 다른 Tk에서 그대로 들어와도 0으로 잘려 사라지지 않도록 여기서도 모은다
        self._wheel_acc += -event.delta / 120 * 60
        steps = int(self._wheel_acc)
        self._wheel_acc -= steps
        if steps:
            canvas.yview_scroll(steps, "units")
            self._drop_hidden_focus()

    def _drop_hidden_focus(self):
        """휠로 입력칸이 가려지면 포커스를 뺀다 (스크롤바를 클릭했을 때와 같다). 숨은 값이 입력되거나 방향키 조그가 말없이 막히는 일을 막는다."""
        # focus_get()은 콤보박스 목록이 열려 있으면 KeyError가 나므로 Tcl에 직접 묻는다
        canvas, focused = self.sidebar_canvas, str(self.root.tk.call("focus"))
        for ent in self._entries:
            if str(ent) == focused:
                top = ent.winfo_rooty() - self.sidebar_inner.winfo_rooty()
                view_top = canvas.canvasy(0)
                if top < view_top or top + ent.winfo_height() > view_top + canvas.winfo_height():
                    self.root.focus_set()

    def _on_mousewheel_break(self, event):
        self._on_mousewheel(event)
        return "break"

    def _bind_focus_scroll(self, widget):
        for child in widget.winfo_children():
            # Frame에는 걸지 않는다. 자식이 포커스를 받으면 조상 Frame에도 FocusIn이 와서 이미 보이는 입력칸을 눌러도 패널이 움직인다
            if not isinstance(child, tk.Frame):
                child.bind("<FocusIn>", lambda _e, w=child: self._scroll_into_view(w), add="+")
            self._bind_focus_scroll(child)

    def _scroll_into_view(self, widget):
        """Tab으로 화면 밖 입력칸에 들어가 보지 못한 채 입력하는 일이 없도록 위젯이 보이게 스크롤한다."""
        canvas, inner = self.sidebar_canvas, self.sidebar_inner
        content_h, view_h = inner.winfo_reqheight(), canvas.winfo_height()
        if self.jogging or content_h <= view_h:
            return
        top = widget.winfo_rooty() - inner.winfo_rooty()
        bottom = top + widget.winfo_height()
        view_top = canvas.canvasy(0)
        if top < view_top:
            canvas.yview_moveto(max(top - 8, 0) / content_h)
        elif bottom > view_top + view_h:
            canvas.yview_moveto(min(bottom + 8 - view_h, content_h - view_h) / content_h)

    # 메인 -----------------------------------------------------------------
    def _build_main(self):
        top = tk.Frame(self.main, padx=24, pady=16)
        top.pack(fill=tk.X)
        self.metric_row = top
        self._main_frames = [top]

        # 상태 배지가 잘리지 않도록 가장 먼저 배치한다 (pack은 먼저 넣은 위젯부터 공간을 준다).
        # csv 라벨은 폭을 요구하지 않고(width=1) 지표가 쓰고 남은 공간만 차지한다. 공간이 모자라면 파일 이름의 뒷부분이 보인다
        right = tk.Frame(top); right.pack(side=tk.RIGHT, fill=tk.X, expand=True); self._main_frames.append(right)
        self.state_badge = tk.Label(right, text="  READY  ", font=(UI_FONT, 10, "bold"), padx=14, pady=8)
        self.state_badge.pack(anchor="e")
        self.csv_label = tk.Label(right, text="", font=(self.colors["mono"], 8), anchor="e", width=1)
        self.csv_label.pack(**CSV_LABEL_PACK)

        left = tk.Frame(top); left.pack(side=tk.LEFT); self._main_frames.append(left)
        cap = tk.Label(left, text="LIVE FORCE", font=(UI_FONT, 8, "bold"), anchor="w"); cap.pack(anchor="w")
        self.force_label = tk.Label(left, text="0.0000 N", font=(self.colors["mono"], 34, "bold"), anchor="w")
        self.force_label.pack(anchor="w")
        self.grams_label = tk.Label(left, text="0.0 g (raw)", font=(self.colors["mono"], 10), anchor="w")
        self.grams_label.pack(anchor="w")
        self._captions = [cap]

        mid = tk.Frame(top); mid.pack(side=tk.LEFT, padx=40); self._main_frames.append(mid)
        self.metric_mid = mid
        cap2 = tk.Label(mid, text="PEAK", font=(UI_FONT, 8, "bold"), anchor="w"); cap2.pack(anchor="w")
        self.peak_label = tk.Label(mid, text="0.0000 N", font=(self.colors["mono"], 18), anchor="w"); self.peak_label.pack(anchor="w")
        cap3 = tk.Label(mid, text="DISTANCE / TIME", font=(UI_FONT, 8, "bold"), anchor="w"); cap3.pack(anchor="w", pady=(6, 0))
        self.progress_label = tk.Label(mid, text="0.00 mm  ·  0.0 s", font=(self.colors["mono"], 12), anchor="w"); self.progress_label.pack(anchor="w")
        self._captions += [cap2, cap3]

        mid2 = tk.Frame(top); mid2.pack(side=tk.LEFT, padx=(0, 40)); self._main_frames.append(mid2)
        self.metric_mid2 = mid2
        cap4 = tk.Label(mid2, text="STRESS / STRAIN", font=(UI_FONT, 8, "bold"), anchor="w"); cap4.pack(anchor="w")
        self.stress_label = tk.Label(mid2, text="0.000 kPa  ·  0.0 %", font=(self.colors["mono"], 18), anchor="w"); self.stress_label.pack(anchor="w")
        self.modulus_caption = tk.Label(mid2, text=MODULUS_CAPTION, font=(UI_FONT, 8, "bold"), anchor="w"); self.modulus_caption.pack(anchor="w", pady=(6, 0))
        self.modulus_label = tk.Label(mid2, text="-", font=(self.colors["mono"], 12), anchor="w"); self.modulus_label.pack(anchor="w")
        self._captions += [cap4, self.modulus_caption]

        # 진행률 게이지: 상단 지표 아래 한 줄 전체
        prog = tk.Frame(self.main, padx=24, pady=4); prog.pack(fill=tk.X); self._main_frames.append(prog)
        cap6 = tk.Label(prog, text="PROGRESS", font=(UI_FONT, 8, "bold"), anchor="w", width=10); cap6.pack(side=tk.LEFT)
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(prog, style="Hydro.Horizontal.TProgressbar", orient="horizontal", mode="determinate", maximum=100.0, variable=self.progress_var)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 12))
        self.percent_label = tk.Label(prog, text="0 %", font=(self.colors["mono"], 11, "bold"), anchor="w", width=26)
        self.percent_label.pack(side=tk.LEFT)
        self._captions.append(cap6)

        graph_host = tk.Frame(self.main, padx=16, pady=8)
        graph_host.pack(fill=tk.BOTH, expand=True)
        self._main_frames.append(graph_host)
        self.figure = Figure(figsize=(7, 5), dpi=100)
        self.axes = self.figure.add_subplot(111)
        (self.line,) = self.axes.plot([], [], linewidth=1.8)
        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_host)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.get_tk_widget().configure(highlightthickness=0)
        self.canvas.mpl_connect("resize_event", lambda e: self._set_graph_margins(e.width, e.height))
        self._set_graph_margins(*self.canvas.get_width_height())
        self.main.bind("<Configure>", self._on_main_resize)

    def _set_graph_margins(self, w, h):
        """여백을 비율이 아니라 픽셀로 고정한다. 창이 낮아져도 눈금 글자와 x축 이름이 잘리지 않는다."""
        if w >= 200 and h >= 150:
            self.figure.subplots_adjust(left=72 / w, right=1 - 18 / w, top=1 - 42 / h, bottom=52 / h)

    def _on_main_resize(self, event):
        """좁거나 낮은 창에서는 상단 지표를 줄인다. 넓은 창의 글자 크기와 간격은 그대로다."""
        # 기준 폭 990: 시험 중 값은 0일 때(891 px)보다 넓어 최대 약 985 px가 필요하다. 1366 폭 노트북(메인 999)은 축소하지 않는다
        compact = event.width < 990 or event.height < 600
        if compact == self._compact:
            return
        self._compact = compact
        mono = self.colors["mono"]
        self.force_label.configure(font=(mono, 22 if compact else 34, "bold"))
        self.peak_label.configure(font=(mono, 13 if compact else 18))
        self.stress_label.configure(font=(mono, 13 if compact else 18))
        self.progress_label.configure(font=(mono, 10 if compact else 12))
        self.modulus_label.configure(font=(mono, 9 if compact else 12))
        self.percent_label.configure(font=(mono, 9 if compact else 11, "bold"), width=20 if compact else 26)
        self.modulus_caption.configure(text=MODULUS_CAPTION_COMPACT if compact else MODULUS_CAPTION)
        gap = 8 if compact else 40
        self.metric_mid.pack_configure(padx=gap)
        self.metric_mid2.pack_configure(padx=(0, gap))
        self.metric_row.configure(padx=8 if compact else 24, pady=6 if compact else 16)
        if compact:
            self.csv_label.pack_forget()
        else:
            self.csv_label.pack(**CSV_LABEL_PACK)

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
        for widget in (self.sidebar_host, self.sidebar_canvas, self.sidebar_inner, self.sidebar_footer):
            widget.configure(bg=c["sidebar"])
        self.footer_sep.configure(bg=c["border"])
        self.style.configure("Hydro.Vertical.TScrollbar", troughcolor=c["sidebar"], bordercolor=c["sidebar"], lightcolor=c["sidebar"], darkcolor=c["sidebar"], background=c["muted"], gripcount=0, arrowsize=10)
        self.style.map("Hydro.Vertical.TScrollbar", background=[("active", c["text"])])
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
        self.percent_label.configure(bg=c["bg"], fg=c["text_strong"])
        self.style.configure("Hydro.Horizontal.TProgressbar", troughcolor=c["input"], background=c["accent"], bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"], thickness=14)
        self.eta_label.configure(bg=c["sidebar"], fg=c["muted"])
        self._refresh_eta()
        self.stress_label.configure(bg=c["bg"], fg=c["text"])
        self.modulus_label.configure(bg=c["bg"], fg=c["text"])
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
        stress = self.plot_var.get() == "STRESS"
        if self.mode_var.get() == "COMPRESSION":
            try:
                d = float(self.distance_var.get())
            except ValueError:
                d = 5.0
            if stress:
                try:
                    h0 = float(self.height_var.get())
                except ValueError:
                    h0 = 0.0
                xmax = d / h0 if h0 > 0 and d > 0 else 1.0
                self.axes.set_xlim(0, min(xmax * 1.05, 1.0))
                self.axes.set_xlabel("Strain (-)", color=c["muted"])
                self.axes.set_title("Compressive modulus  ·  Stress vs Strain", color=c["text"], loc="left", fontsize=11, pad=12)
            else:
                self.axes.set_xlim(0, d * 1.05 if d > 0 else 5.0)
                self.axes.set_xlabel("Distance (mm)", color=c["muted"])
                self.axes.set_title("Compressive strength  ·  Force vs Distance", color=c["text"], loc="left", fontsize=11, pad=12)
        else:
            self.axes.set_xlim(0, 60)
            self.axes.set_xlabel("Time (s)", color=c["muted"])
            self.axes.set_title("Poroelasticity / Stress relaxation  ·  " + ("Stress" if stress else "Force") + " vs Time", color=c["text"], loc="left", fontsize=11, pad=12)
        self.axes.set_ylabel("Stress (kPa)" if stress else "Force (N)", color=c["muted"])
        self.axes.set_ylim(-0.01, 2.0 if stress else 0.2)
        self.canvas.draw_idle()

    def _on_plot_change(self):
        """그래프 종류 전환. 시험 중이면 축만 다시 잡고 데이터는 유지한다."""
        was = self.is_running
        self.is_running = False
        self._reset_axes()
        self.is_running = was

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
            "FINDING": c["yellow"], "STOPPING": c["yellow"], "ZEROING": c["yellow"], "START SET": c["green"],
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
            diameter=self.diameter_var.get().strip(),
            height=self.height_var.get().strip(),
            start_force=self.start_force_var.get().strip(),
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
    def _write_motor(self, command):
        sent_at = time.monotonic()
        result = self.arduino.write(command)
        self._step_log_record("TX", sent_at, detail=command.decode("ascii", errors="replace").strip(),
                              write_duration_us=round((time.monotonic() - sent_at) * 1_000_000))
        return result

    def _step_log_record(self, event, received_at=None, **values):
        if self._step_log:
            try:
                self._step_log.record(event, received_at, **values)
            except OSError as exc:
                self._end_step_log(f"저장 실패: {exc}", error=True, send_off=True)

    def toggle_step_log(self):
        if self._step_log:
            try:
                self._step_log_phase = "stopping"
                self._step_log_phase_at = time.monotonic()
                self.step_log_button.configure(state=tk.DISABLED)
                self._write_motor(b"DIAG,OFF\n")
            except Exception as exc:
                self._end_step_log(f"기록 종료 통신 실패: {exc}", error=True)
            return
        if not self._motor_ready():
            self._message("장비를 먼저 연결하세요. STEP 로그는 기본 OFF입니다.", "warn")
            return
        try:
            folder = Path(self.save_dir_var.get().strip() or ".")
            folder.mkdir(parents=True, exist_ok=True)
            self.step_log_path = folder / f"STEP_4F_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
            self._step_log = StepLog(self.step_log_path, demo=self.demo)
            self._step_log_phase = "starting"
            self._step_log_phase_at = time.monotonic()
            self.step_log_button.configure(state=tk.DISABLED, text="확인 중…")
            self._write_motor(b"DIAG,ON\n")
            self._message("STEP 로그 지원 펌웨어를 확인하는 중…")
        except Exception as exc:
            self._end_step_log(f"STEP 로그 시작 실패: {exc}", error=True, send_off=True)

    def _end_step_log(self, reason, *, error=False, send_off=False):
        log = self._step_log
        self._step_log = None
        self._step_log_phase = None
        self.step_log_button.configure(state=tk.NORMAL, text="STEP 로그")
        if send_off and self._motor_ready():
            try:
                self.arduino.write(b"DIAG,OFF\n")
            except Exception:
                pass
        if log:
            try:
                log.close(reason)
            except OSError as exc:
                reason = f"STEP 로그 저장 실패: {exc}"
                error = True
        self._message(reason, "error" if error else "ok")

    def _check_step_log(self):
        if not self._step_log:
            return
        now = time.monotonic()
        if self._step_log_phase == "starting" and now - self._step_log_phase_at > 3:
            self._end_step_log("STEP 로그 응답 없음. 새 진단 펌웨어를 Arduino에 업로드하세요.", error=True, send_off=True)
        elif self._step_log_phase == "stopping" and now - self._step_log_phase_at > 3:
            self._end_step_log("STEP 로그 종료 응답 없음. 파일을 닫았습니다(기록 누락 가능).", error=True, send_off=True)
        elif self._step_log_phase == "active" and now - self._step_log.last_rx > 3:
            self._end_step_log("STEP 로그 수신이 끊겼습니다. 파일을 닫았습니다.", error=True, send_off=True)

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
        self._set_start_point_phase(None)
        self._last_loadcell_at = 0.0
        self.jogging = False
        if self.is_running:
            self.stop_test()
        if self._motor_ready():
            try:
                self._write_motor(stop_command())
                time.sleep(0.05)
            except Exception:
                pass
        if self._step_log:
            self._end_step_log("연결 해제로 STEP 로그를 저장했습니다(종료 응답 미확인).", send_off=True)
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
            except Exception as exc:
                self.rx_queue.put(("serial_error", f"{tag}: {exc}", time.monotonic()))
                break
            if line:
                self.rx_queue.put((tag, line, time.monotonic()))

    def _pump_queue(self):
        if self.closing:
            return
        while True:
            try:
                tag, line, received_at = self.rx_queue.get_nowait()
            except queue.Empty:
                break
            if tag == "loadcell":
                self._on_loadcell_line(line, received_at)
            elif tag == "serial_error":
                if self._step_log:
                    self._end_step_log(f"통신 오류로 STEP 로그 종료: {line}", error=True, send_off=True)
                if self._starting_point:
                    self._stop_start_point(f"탐색 중 통신 오류: {line}", "error")
            else:
                self._on_motor_line(line, received_at)
        self._check_start_point()
        self._check_step_log()
        self._check_move_timeout()
        self._schedule(30, self._pump_queue)

    # ------------------------------------------------------------------ 수신 처리
    def _on_motor_line(self, line, received_at=None):
        received_at = time.monotonic() if received_at is None else received_at
        if self._step_log:
            try:
                kind = self._step_log.receive(line, received_at)
                if kind == "ON":
                    self._step_log_phase = "active"
                    self.step_log_button.configure(state=tk.NORMAL, text="로그 중지")
                    self._message(f"STEP 로그 기록 중: {self.step_log_path.name}", "ok")
                elif kind == "OFF":
                    self._end_step_log(f"STEP 로그 저장 완료: {self.step_log_path.name}")
                elif line in ("READY", "ARDUINO_READY") and self._step_log.active:
                    self._end_step_log("Arduino 재시작으로 STEP 로그를 종료했습니다.", error=True, send_off=True)
            except (OSError, ValueError) as exc:
                self._end_step_log(f"STEP 로그 오류: {exc}", error=True, send_off=True)
        if line.startswith("DBG,"):
            return
        if line == "STOPPED" and self._starting_point == "stopping" and received_at >= self._start_phase_at:
            self._set_start_point_phase("zeroing")
            self._set_state("ZEROING")
            self._message("정지 확인. 다음 하중을 받아 자동으로 0점을 설정합니다.")
            return
        if line == "DONE":
            self._on_move_done()
        elif line == "STOP_BUTTON":
            self._set_start_point_phase(None)
            self.jogging = False
            self._finish_test("STOPPED")
            self._message("하드웨어 비상 정지 버튼이 눌렸습니다.", "warn")
        elif line == "MOTOR_OFF":
            if self._starting_point:
                self._stop_start_point("모터 전원이 꺼져 탐색을 취소했습니다.", "warn")
            self.motor_enabled = False
            self._refresh_motor_status()
        elif line == "MOTOR_ON":
            self.motor_enabled = True
            self._refresh_motor_status()

    def _on_loadcell_line(self, line, received_at=None):
        grams = parse_indicator_grams(line)
        if grams is None:
            return
        received_at = time.monotonic() if received_at is None else received_at
        self._last_loadcell_at = received_at
        # 인디케이터의 입력 부호를 유지해 N으로 환산한다.
        self.current_grams = grams
        force = grams_to_newton(grams) - self.force_offset_N
        self._step_log_record("LOAD", received_at, grams=grams, force_N=force, detail=line)
        self.force_label.configure(text=f"{force:.4f} N")
        self.grams_label.configure(text=f"{grams:.1f} g (raw)")
        if abs(grams) > self.profile.max_load_g * 0.9:
            if self._starting_point:
                self._stop_start_point("로드셀 용량 90% 초과로 탐색을 중지했습니다.", "error")
                return
            self._message(f"하중이 로드셀 용량({self.profile.loadcell})의 90%를 넘었습니다!", "error")
        if self._starting_point:
            if time.monotonic() - received_at > START_POINT_TIMEOUT:
                self._stop_start_point("하중 데이터가 지연되어 탐색을 중지했습니다.", "error")
                return
            if self._starting_point == "seeking" and force >= self._start_force_N:
                self._request_start_point_stop()
            elif self._starting_point == "zeroing" and received_at >= self._start_phase_at:
                self._set_start_point_phase(None)
                self.zero_force()
                self._set_state("START SET")
                self._message("Starting point 설정 완료. 정지 후 하중을 0점으로 맞췄습니다.", "ok")
                return
        if self.is_running:
            self._live_distance = self._commanded_distance()
            self._show_live_stress(force, self._live_distance, self.area_mm2, self.height0_mm)
            self._accumulate(force)
        else:
            # 기록 중이 아닐 때도 현재 입력된 시편 치수로 응력을 계속 표시한다.
            self._show_live_stress(force, self._live_distance)

    def _live_area_mm2(self):
        """현재 입력된 시편 직경으로 단면적(mm²)을 계산한다. 잘못된 값이면 0."""
        try:
            d = float(self.diameter_var.get())
        except ValueError:
            return 0.0
        return math.pi * (d / 2.0) ** 2 if d > 0 else 0.0

    def _live_height_mm(self):
        try:
            h = float(self.height_var.get())
        except ValueError:
            return 0.0
        return h if h > 0 else 0.0

    def _show_live_stress(self, force, distance, area=None, h0=None):
        """응력(kPa)·변형률 표시. 시험 중에는 시작 시 고정한 단면적·높이, 그 외에는 입력칸 값을 쓴다."""
        if area is None:
            area = self._live_area_mm2()
        if h0 is None:
            h0 = self._live_height_mm()
        stress_kPa = force / (area * 1e-6) / 1000.0 if area > 0 else 0.0
        strain = distance / h0 if h0 > 0 else 0.0
        self.stress_label.configure(text=f"{stress_kPa:.3f} kPa  ·  {strain * 100:.1f} %")
        return stress_kPa, strain

    def _refresh_eta(self):
        """목표 거리 ÷ 이동 속도 = 예상 압축 시간."""
        try:
            d = float(self.distance_var.get()); v = float(self.speed_var.get())
            if v > self.profile.max_speed_mm_s:
                self.eta_label.configure(text=f"이동 속도는 최대 {self.profile.max_speed_mm_s:g} mm/s (펌웨어 상한)")
                return
            if d > 0 and v > 0:
                t = move_duration(d, v, self.profile.accel_mm_s2)
                txt = f"예상 압축 시간  {t:.1f} s" if t < 120 else f"예상 압축 시간  {t / 60:.1f} min ({t:.0f} s)"
                self.eta_label.configure(text=txt)
                return
        except ValueError:
            pass
        self.eta_label.configure(text="예상 압축 시간  -")

    def _refresh_progress(self):
        """이동 진행률(%)과 남은 시간을 게이지에 반영한다."""
        if not self.is_running or self.target_dist <= 0:
            return
        elapsed = time.time() - self.start_time
        if self.motor_moving:
            pct = min(self._commanded_distance(elapsed) / self.target_dist, 1.0) * 100.0
            remain = max(self.expected_move_s - elapsed, 0.0)
            self.percent_label.configure(text=f"{pct:3.0f} %  ·  남은 {remain:.0f} s")
        else:
            pct = 100.0
            self.percent_label.configure(text="100 %  ·  이동 완료" if self.mode_var.get() == "COMPRESSION" else f"100 %  ·  유지 {elapsed - self.expected_move_s:.0f} s")
        self.progress_var.set(pct)

    # ------------------------------------------------------------------ 기록 속도(평균)
    def _sample_interval(self):
        """기록 간격(초). 0이면 원본 전부 기록."""
        try:
            rate = float(self.sample_rate_var.get())
        except (ValueError, AttributeError):
            rate = 10.0
        return 1.0 / rate if rate > 0 else 0.0

    def _commanded_distance(self, elapsed=None):
        """시각 elapsed(초)의 명령 위치(mm). 펌웨어의 가속·등속·감속 프로파일을 그대로 따른다."""
        if not self.motor_moving:
            return self.target_dist
        if elapsed is None:
            elapsed = time.time() - self.start_time
        return position_at(elapsed, self.target_dist, self.test_speed, self.profile.accel_mm_s2)

    def _accumulate(self, force):
        """로드셀 한 줄을 (시각, 위치, 힘)으로 버킷에 넣고, 기록 간격이 차면 세 값을 함께 평균해 기록한다."""
        now = time.time()
        elapsed = now - self.start_time
        distance = self._commanded_distance(elapsed)
        interval = self._sample_interval()
        if interval <= 0:
            self._record(elapsed, distance, force)
            return
        self._bucket.append((elapsed, distance, force))
        if now - self._bucket_t0 >= interval:
            self._flush_bucket()
            self._bucket_t0 += interval * int((now - self._bucket_t0) / interval)

    def _flush_bucket(self):
        if self._bucket:
            n = len(self._bucket)
            self._record(sum(b[0] for b in self._bucket) / n,
                         sum(b[1] for b in self._bucket) / n,
                         sum(b[2] for b in self._bucket) / n)
            self._bucket = []

    # ------------------------------------------------------------------ GitHub 업데이트
    def check_for_update(self):
        if self._starting_point:
            self._message("Starting point 탐색을 정지한 뒤 업데이트를 확인하세요.", "warn")
            return
        self.update_button.configure(state=tk.DISABLED, text="⬇  확인 중…")
        self._message("GitHub에서 최신 버전을 확인하는 중…")
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self):
        try:
            info = updater.fetch_latest()
        except Exception as exc:
            self.root.after(0, self._update_failed, f"버전 확인 실패: {exc}")      # lambda 안에서 exc를 쓰면 except가 끝난 뒤 NameError가 난다
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
                self.root.after(0, self._update_failed, f"다운로드 실패: {exc}")
                return
            self.root.after(0, lambda: self._download_done(path, exe))

        threading.Thread(target=worker, daemon=True).start()

    def _download_done(self, path, exe):
        self.update_button.configure(state=tk.NORMAL, text=f"⬇  업데이트 확인  (v{__version__})")
        self._message(f"다운로드 완료: {path}", "ok")
        if exe and not self.is_running and not self._starting_point and self.show_dialogs and messagebox.askyesno(
            "업데이트 설치",
            f"다운로드했습니다:\n{path}\n\n지금 프로그램을 닫고 새 버전으로 교체한 뒤 다시 실행할까요?\n(기존 파일은 .bak 으로 남습니다. 시험 중이면 '아니오'를 누르세요.)",
            parent=self.root,
        ):
            updater.replace_and_restart(path, exe)
            self.close()
        elif self.is_running or self._starting_point:
            self._notify("showinfo", "업데이트", f"새 exe를 저장했습니다:\n{path}\n시험이 끝난 뒤 프로그램을 닫고 이 파일로 바꿔 실행하세요.")
        elif not exe:
            self._notify("showinfo", "업데이트", f"새 exe를 저장했습니다:\n{path}\n(개발 환경에서는 자동 교체를 하지 않습니다.)")

    def _record(self, elapsed, distance, force):
        strain = distance / self.height0_mm if self.height0_mm > 0 else 0.0
        stress_kPa = force / (self.area_mm2 * 1e-6) / 1000.0 if self.area_mm2 > 0 else 0.0
        self.time_data.append(elapsed)
        self.dist_data.append(distance)
        self.force_data.append(force)
        self.strain_data.append(strain)
        self.stress_data.append(stress_kPa)
        self.peak_force_N = max(self.peak_force_N, force)
        self.peak_label.configure(text=f"{self.peak_force_N:.4f} N")
        self.progress_label.configure(text=f"{distance:.2f} mm  ·  {elapsed:.1f} s")
        self.stress_label.configure(text=f"{stress_kPa:.3f} kPa  ·  {strain * 100:.1f} %")
        self.sb_samples.configure(text=f"{len(self.time_data)} samples · {self.sample_rate_var.get()}/s")
        self._refresh_progress()
        if len(self.time_data) % 5 == 0:
            self._update_modulus()
        if self.csv_writer:
            self.csv_writer.writerow([round(elapsed, 3), round(distance, 4), round(force, 4), round(strain, 5), round(stress_kPa, 4)])
            self.csv_file.flush()      # 장시간 시험 중 프로그램이 죽어도 그때까지의 데이터는 남긴다

    def _update_modulus(self, lo=0.05, hi=0.15):
        """변형률 lo~hi 구간의 응력-변형률 최소제곱 직선 기울기(kPa)를 압축 탄성률로 표시한다."""
        if self.mode_var.get() != "COMPRESSION":
            self.modulus_kPa = None
            self.modulus_label.configure(text="-  (압축 강도 모드에서만 계산)")
            return
        pts = [(e, s) for e, s in zip(self.strain_data, self.stress_data) if lo <= e <= hi]
        if len(pts) < 4 or pts[-1][0] - pts[0][0] < (hi - lo) * 0.4:
            self.modulus_kPa = None
            self.modulus_label.configure(text="-  (5-15 % 변형률 구간 데이터 부족)")
            return
        n = len(pts)
        mx = sum(e for e, _ in pts) / n
        my = sum(v for _, v in pts) / n
        sxx = sum((e - mx) ** 2 for e, _ in pts)
        sxy = sum((e - mx) * (v - my) for e, v in pts)
        self.modulus_kPa = sxy / sxx if sxx > 0 else None
        if self.modulus_kPa is None:
            self.modulus_label.configure(text="-")
        else:
            self.modulus_label.configure(text=f"E = {self.modulus_kPa:.2f} kPa   ({n} pts)")

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
    def _set_start_point_phase(self, phase):
        if phase is None and self._starting_point is None:
            return
        self._starting_point = phase
        self._start_phase_at = time.monotonic()
        state = tk.DISABLED if phase else tk.NORMAL
        for widget in (self.start_point_button, self.start_force_entry, self.start_button,
                       self.press_button, self.release_button, self.zero_button, self.motor_on_button,
                       self.dir_button, self.save_defaults_button, self.update_button):
            widget.configure(state=state)

    def find_start_point(self):
        if self._starting_point or self.is_running or self.jogging or self.motor_moving:
            self._message("이동 또는 시험을 먼저 정지하세요.", "warn")
            return
        if not self._motor_ready() or not self.indicator or not self.indicator.is_open:
            self._message("모터와 로드셀을 먼저 연결하세요.", "warn")
            return
        if time.monotonic() - self._last_loadcell_at > START_POINT_TIMEOUT:
            self._message("최근 하중 데이터가 없습니다. 로드셀 수신을 확인하세요.", "warn")
            return
        if str(self.update_button.cget("state")) == tk.DISABLED:
            self._message("업데이트 확인 또는 다운로드가 끝난 뒤 탐색하세요.", "warn")
            return
        try:
            force = float(self.start_force_var.get())
            max_force = grams_to_newton(self.profile.max_load_g * 0.9)
            if not math.isfinite(force) or not 0 < force < max_force:
                raise ValueError(f"Starting point 하중은 0보다 크고 {max_force:.3f} N보다 작아야 합니다.")
            self._save_settings(quiet=True)
        except (ValueError, OSError) as exc:
            self._message(str(exc), "error")
            return
        if abs(self.current_grams) > self.profile.max_load_g * 0.9:
            self._message("하중이 로드셀 용량의 90%를 넘었습니다. 탐색할 수 없습니다.", "error")
            return
        self._start_force_N = force
        self._set_start_point_phase("seeking")
        if grams_to_newton(self.current_grams) - self.force_offset_N >= force:
            self._request_start_point_stop()
            return
        try:
            self._write_motor(jog_command(self.profile.press_cmd, START_POINT_SPEED))
        except Exception as exc:
            self._stop_start_point(f"탐색 시작 실패: {exc}", "error")
            return
        self.motor_enabled = True
        self._refresh_motor_status()
        self._set_state("FINDING")
        self._message(f"Starting point 탐색: {force:g} N 이상에서 정지 · 속도 {START_POINT_SPEED:g} mm/s")

    def _request_start_point_stop(self):
        self._set_start_point_phase("stopping")
        try:
            self._write_motor(stop_command())
        except Exception as exc:
            self._stop_start_point(f"정지 명령 전송 실패: {exc}", "error")
            return
        self._set_state("STOPPING")
        self._message("기준 하중 도달. 모터의 정지 응답을 기다립니다.")

    def _stop_start_point(self, message, level="warn"):
        self._set_start_point_phase(None)
        try:
            if self._motor_ready():
                self._write_motor(stop_command())
        except Exception as exc:
            message += f" 정지 명령 전송 실패: {exc}"
            level = "error"
        self._set_state("STOPPED")
        self._message(message, level)

    def _check_start_point(self):
        if not self._starting_point:
            return
        now = time.monotonic()
        if not self._motor_ready() or not self.indicator or not self.indicator.is_open:
            self._stop_start_point("연결이 끊겨 탐색을 중지했습니다.", "error")
        elif now - self._last_loadcell_at > START_POINT_TIMEOUT:
            self._stop_start_point("2초 동안 유효한 하중이 없어 탐색을 중지했습니다.", "error")
        elif self._starting_point != "seeking" and now - self._start_phase_at > START_POINT_TIMEOUT:
            self._stop_start_point("정지 또는 0점 설정 응답 시간이 초과되었습니다.", "error")

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
        if self._starting_point:
            self._message("Starting point 탐색 중에는 수동 이동을 사용할 수 없습니다.", "warn")
            return
        if not self._motor_ready():
            self._message("장비가 연결되어 있지 않습니다.", "warn")
            return
        if self.is_running:
            self._message("시험 중에는 조그를 사용할 수 없습니다.", "warn")
            return
        try:
            if float(self.jog_speed_var.get()) > self.profile.max_speed_mm_s:
                raise ValueError(f"이동 속도는 최대 {self.profile.max_speed_mm_s:g} mm/s 입니다 (펌웨어 상한).")
            self._write_motor(jog_command(cmd, self.jog_speed_var.get()))
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
            self._write_motor(stop_command())
        self._set_state("READY" if not self.is_running else self._state)

    def zero_force(self):
        if self._starting_point:
            self._message("Starting point 탐색 중에는 0점을 바꿀 수 없습니다.", "warn")
            return
        if self.is_running:
            self._message("시험 중에는 0점을 바꿀 수 없습니다. 바꾸면 기록된 힘이 어긋납니다.", "warn")
            return
        self.force_offset_N = grams_to_newton(self.current_grams)
        self.force_label.configure(text="0.0000 N")
        self._live_distance = 0.0
        self.stress_label.configure(text="0.000 kPa  ·  0.0 %")
        self._message(f"0점 설정 (raw {self.current_grams:.1f} g)", "ok")

    # ------------------------------------------------------------------ 시험
    def start_test(self):
        if self._starting_point:
            self._message("Starting point 탐색을 완료하거나 정지한 뒤 시험을 시작하세요.", "warn")
            return
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
            if self.test_speed > self.profile.max_speed_mm_s:
                raise ValueError(f"이동 속도는 최대 {self.profile.max_speed_mm_s:g} mm/s 입니다 (펌웨어 상한). 더 빠르게 보내면 펌웨어가 속도를 잘라 거리 추정이 어긋납니다.")
            diameter = float(self.diameter_var.get())
            self.height0_mm = float(self.height_var.get())
            if diameter <= 0 or self.height0_mm <= 0:
                raise ValueError("시편 직경과 초기 높이는 0보다 커야 합니다.")
            if self.target_dist >= self.height0_mm:
                raise ValueError("목표 거리가 시편 높이 이상입니다. 압축판이 바닥에 닿습니다.")
            self.area_mm2 = math.pi * (diameter / 2.0) ** 2
            self._open_csv()
        except (OSError, ValueError) as exc:
            self._message(str(exc), "error")
            self._notify("showerror", "시작 오류", str(exc))
            return
        self.time_data.clear(); self.dist_data.clear(); self.force_data.clear()
        self.strain_data.clear(); self.stress_data.clear()
        self.line.set_data([], [])
        self.peak_force_N = 0.0
        self.modulus_kPa = None
        self.modulus_label.configure(text="-")
        self.stress_label.configure(text="0.000 kPa  ·  0.0 %")
        self.progress_var.set(0.0)
        self.percent_label.configure(text="0 %")
        self._reset_axes()
        self.expected_move_s = move_duration(self.target_dist, self.test_speed, self.profile.accel_mm_s2)
        self._live_distance = 0.0
        self.start_time = time.time()
        self._bucket = []
        self._bucket_t0 = self.start_time
        self.motor_moving = True
        self.is_running = True
        self._running_mode = self.mode_var.get()
        self.motor_enabled = True
        self._refresh_motor_status()
        self._write_motor(command)
        self._set_state("RUNNING")
        self._message(f"시험 시작: {self.target_dist:g} mm @ {self.test_speed:g} mm/s, 시편 D{diameter:g} x H{self.height0_mm:g} mm (A={self.area_mm2:.2f} mm2, 최대 변형률 {self.target_dist / self.height0_mm * 100:.0f} %)", "ok")
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
        self.csv_file.write(f"# specimen: cylinder, diameter_mm={float(self.diameter_var.get()):g}, height0_mm={self.height0_mm:g}, area_mm2={self.area_mm2:.4f}, mode={self.mode_var.get()}, stress=engineering F/A0, strain=distance/height0, distance=commanded trapezoid accel_mm_s2={self.profile.accel_mm_s2:g}\r\n")
        self.csv_writer.writerow(["Time (s)", "Distance (mm)", "Force (N)", "Strain (-)", "Stress (kPa)"])
        self.csv_label.configure(text=f"기록 중: {path.name}")

    def _close_csv(self):
        if self.csv_file and not self.csv_file.closed:
            self.csv_file.close()
        self.csv_file = self.csv_writer = None

    def _finish_test(self, state):
        was_running = self.is_running
        if was_running:
            self._update_modulus()
            self._refresh_progress()
        if was_running:
            self._flush_bucket()
        self.is_running = False
        self.motor_moving = False
        self._close_csv()
        self._set_state(state)
        if was_running and self.csv_path:
            self.csv_label.configure(text=f"저장됨: {self.csv_path.name}")

    def stop_test(self):
        if self._starting_point:
            self._stop_start_point("Starting point 탐색을 취소했습니다.")
            return
        # 키 뗌을 놓쳐도 플래그가 남지 않게 한다. jog_key_active는 두어야 아직 누른 키의 자동 반복이 조그를 다시 시작하지 않는다
        self.jogging = False
        if self._motor_ready():
            self._write_motor(stop_command())
        if self.is_running:
            self._finish_test("STOPPED")
            self._message(f"기록 종료. {len(self.time_data)} 샘플 저장.", "ok")
        else:
            self._set_state("READY")

    def emergency_stop(self):
        self._set_start_point_phase(None)
        self.jogging = False
        self.jog_key_active = None
        if self._motor_ready():
            self._write_motor(stop_command())
        self._finish_test("E-STOP")
        self._message("비상 정지: 모터 이동을 멈췄습니다. 드라이버는 현재 위치를 유지합니다.", "warn")

    def motor_off(self):
        self._set_start_point_phase(None)
        if not self._motor_ready():
            self._message("장비가 연결되어 있지 않습니다.", "warn")
            return
        self.jogging = False
        self._write_motor(motor_off_command())
        self.motor_enabled = False
        self._refresh_motor_status()
        self._finish_test("MOTOR OFF")
        self._message("모터 전원 OFF: 드라이버 전원을 차단했습니다. 축이 자유롭게 움직일 수 있습니다.", "warn")

    def motor_on(self):
        if self._starting_point:
            return
        if not self._motor_ready():
            self._message("장비가 연결되어 있지 않습니다.", "warn")
            return
        self._write_motor(motor_on_command())
        self.motor_enabled = True
        self._refresh_motor_status()
        self._set_state("READY")
        self._message("모터 전원 ON", "ok")

    def _refresh_plot_labels(self):
        """그래프 선택 라디오 텍스트를 모드에 맞춘다. 압축 강도: X=거리/변형률, Poroelasticity: X=시간."""
        if self.mode_var.get() == "COMPRESSION":
            self.plot_radios["FORCE"].configure(text="힘-거리")
            self.plot_radios["STRESS"].configure(text="응력-변형률")
        else:
            self.plot_radios["FORCE"].configure(text="힘-시간")
            self.plot_radios["STRESS"].configure(text="응력-시간")

    def on_mode_change(self):
        if self.is_running:
            # 시험 중에는 모드를 바꿀 수 없다. 라디오를 원래 값으로 되돌린다.
            self.mode_var.set(self._running_mode)
            self._message("시험 중에는 측정 모드를 바꿀 수 없습니다.", "warn")
            return
        self._refresh_plot_labels()
        self.time_data.clear(); self.dist_data.clear(); self.force_data.clear()
        self.strain_data.clear(); self.stress_data.clear()
        self.line.set_data([], [])
        self.modulus_label.configure(text="-")
        self._reset_axes()

    # ------------------------------------------------------------------ 그래프
    def _update_graph(self):
        if self.closing:
            return
        self._refresh_progress()
        n = len(self.force_data)
        if self.is_running and n:
            stress = self.plot_var.get() == "STRESS"
            if self.mode_var.get() == "COMPRESSION":
                xs = self.strain_data if stress else self.dist_data
            else:
                xs = self.time_data
            ys = self.stress_data if stress else self.force_data
            self.line.set_data(xs[:n], ys[:n])
            xmax = self.axes.get_xlim()[1]
            if xs[-1] > xmax:
                self.axes.set_xlim(0, xs[-1] * 1.25)
            lo, hi = min(ys), max(ys)
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
