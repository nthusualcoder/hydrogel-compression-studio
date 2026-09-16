import argparse
import csv
import os
import queue
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .config import AppSettings, load_settings, save_settings
from .protocol import jog_command, start_command, stop_command
from .serial_io import MemorySerial, open_serial


DARK_COLORS = {
    "bg": "#121212",
    "surface": "#1b1b1b",
    "surface_2": "#242424",
    "border": "#343434",
    "text": "#f2f2f2",
    "muted": "#a6a6a6",
    "accent": "#f46d3b",
    "accent_hover": "#ff8050",
    "green": "#4fb477",
    "red": "#e26868",
    "graph": "#ff7a45",
}

LIGHT_COLORS = {
    "bg": "#f5f5f3",
    "surface": "#ffffff",
    "surface_2": "#f0f0ed",
    "border": "#d7d7d2",
    "text": "#202020",
    "muted": "#686868",
    "accent": "#e85d2f",
    "accent_hover": "#f06f43",
    "green": "#33885a",
    "red": "#b94b4b",
    "graph": "#e85d2f",
}

COLORS = DARK_COLORS.copy()
MOTOR_BAUDRATE = 115200
INDICATOR_BAUDRATE = 115200


class HydrogelTesterApp:
    def __init__(
        self,
        root,
        *,
        serial_factory=open_serial,
        config_path=None,
        demo=False,
        show_dialogs=True,
        initial_theme=None,
    ):
        self.root = root
        self.serial_factory = serial_factory
        self.config_path = config_path
        self.demo = demo
        self.show_dialogs = show_dialogs
        self.settings = load_settings(config_path)
        saved_theme = (
            self.settings.theme if self.settings.theme in {"dark", "light"} else "dark"
        )
        self.theme_name = initial_theme or saved_theme
        COLORS.clear()
        COLORS.update(
            DARK_COLORS if self.theme_name == "dark" else LIGHT_COLORS
        )

        self.arduino = None
        self.indicator = None
        self.is_running = False
        self.motor_reached_target = False
        self.current_weight_g = 0.0
        self.force_offset_N = 0.0
        self.start_time = 0.0
        self.start_motor_time = 0.0
        self.target_dist = 0.0
        self.current_speed = 0.0
        self.csv_filename = ""
        self.csv_file = None
        self.csv_writer = None
        self.current_jog_dir = None
        self.key_release_timer = None
        self.time_data = []
        self.dist_data = []
        self.force_data = []
        self.indicator_queue = queue.Queue()
        self.indicator_after_id = None
        self.update_after_id = None
        self.closing = False

        self._configure_window()
        self._configure_styles()
        self._build_ui()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.indicator_after_id = self.root.after(
            50, self._process_indicator_queue
        )

        if self.demo:
            self.connect_devices()

    def _configure_window(self):
        self.root.title("Hydrogel Compression Studio v2")
        self.root.geometry("1280x800")
        self.root.minsize(1120, 720)
        self.root.configure(bg=COLORS["bg"])

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("App.TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("Card2.TFrame", background=COLORS["surface_2"])
        style.configure(
            "Title.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 20),
        )
        style.configure(
            "Subtitle.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Section.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "Label.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
        )
        style.configure(
            "Value.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
        )
        style.configure(
            "Force.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 28),
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["surface_2"],
            foreground=COLORS["muted"],
            padding=(10, 6),
        )
        style.configure(
            "TEntry",
            fieldbackground=COLORS["surface_2"],
            foreground=COLORS["text"],
            insertcolor=COLORS["text"],
            bordercolor=COLORS["border"],
            padding=8,
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["surface_2"],
            background=COLORS["surface_2"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["border"],
            padding=7,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["surface_2"])],
            selectbackground=[("readonly", COLORS["surface_2"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "Secondary.TButton",
            background=COLORS["surface_2"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            padding=(12, 9),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", COLORS["border"])],
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#ffffff",
            bordercolor=COLORS["accent"],
            padding=(14, 10),
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "Accent.TButton",
            background=[("active", COLORS["accent_hover"])],
        )
        style.configure(
            "Danger.TButton",
            background="#4a2525",
            foreground="#ffb2b2",
            bordercolor="#6a3434",
            padding=(14, 10),
            font=("Segoe UI Semibold", 10),
        )
        style.map("Danger.TButton", background=[("active", "#623030")])
        style.configure(
            "Mode.TRadiobutton",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            indicatorcolor=COLORS["surface_2"],
            padding=(2, 5),
        )
        style.map(
            "Mode.TRadiobutton",
            background=[("active", COLORS["surface"])],
            indicatorcolor=[("selected", COLORS["accent"])],
        )

    def _build_ui(self):
        shell = ttk.Frame(self.root, style="App.TFrame", padding=22)
        shell.pack(fill=tk.BOTH, expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell, style="App.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header, text="Hydrogel Compression Studio", style="Title.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="4층 실험실 · 수직 압축 장비 · Arduino UNO R4 Minima",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.status_label = ttk.Label(
            header, text="●  연결 안 됨", style="Status.TLabel"
        )
        self.status_label.grid(row=0, column=1, rowspan=2, sticky="e")
        self.theme_button = ttk.Button(
            header,
            text=self._theme_button_text(),
            style="Secondary.TButton",
            command=self.toggle_theme,
        )
        self.theme_button.grid(row=0, column=2, rowspan=2, sticky="e", padx=(10, 0))

        sidebar = ttk.Frame(shell, style="App.TFrame", width=360)
        sidebar.grid(row=1, column=0, sticky="nsw", padx=(0, 18))
        sidebar.grid_propagate(False)
        sidebar.configure(width=360)

        self._build_connection_card(sidebar)
        self._build_manual_card(sidebar)

        workspace = ttk.Frame(shell, style="Card.TFrame", padding=18)
        workspace.grid(row=1, column=1, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(2, weight=1)
        self._build_workspace(workspace)

    def _card(self, parent, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(card, text=title, style="Section.TLabel").pack(
            anchor="w", pady=(0, 12)
        )
        return card

    def _field(self, parent, label, variable, *, width=14):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.X, pady=4)
        ttk.Label(frame, text=label, style="Label.TLabel").pack(side=tk.LEFT)
        entry = ttk.Entry(frame, textvariable=variable, width=width)
        entry.pack(side=tk.RIGHT)
        return entry

    def _build_connection_card(self, parent):
        card = self._card(parent, "Connection")
        self.motor_port_var = tk.StringVar(value=self.settings.motor_port)
        self.indicator_port_var = tk.StringVar(value=self.settings.indicator_port)

        self._field(card, "Motor port", self.motor_port_var)
        self._field(card, "Loadcell port", self.indicator_port_var)

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.pack(fill=tk.X, pady=(12, 0))
        self.save_defaults_button = ttk.Button(
            actions,
            text="기본값 저장",
            style="Secondary.TButton",
            command=self.save_defaults,
        )
        self.save_defaults_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.connect_button = ttk.Button(
            actions,
            text="장비 연결",
            style="Accent.TButton",
            command=self.connect_devices,
        )
        self.connect_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

    def _build_manual_card(self, parent):
        card = self._card(parent, "Manual control")
        self.jog_speed_var = tk.StringVar(value="0.5")
        self._field(card, "Jog speed (mm/s)", self.jog_speed_var)

        controls = ttk.Frame(card, style="Card.TFrame")
        controls.pack(fill=tk.X, pady=(10, 0))
        self.down_button = ttk.Button(
            controls, text="↓  하강", style="Secondary.TButton"
        )
        self.down_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.down_button.bind("<ButtonPress-1>", lambda _event: self.start_jog("JU"))
        self.down_button.bind("<ButtonRelease-1>", lambda _event: self.stop_jog())

        self.up_button = ttk.Button(
            controls, text="↑  상승", style="Secondary.TButton"
        )
        self.up_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.up_button.bind("<ButtonPress-1>", lambda _event: self.start_jog("JD"))
        self.up_button.bind("<ButtonRelease-1>", lambda _event: self.stop_jog())

        self.zero_button = ttk.Button(
            card,
            text="현재 하중을 0점으로 설정",
            style="Secondary.TButton",
            command=self.zero_force,
        )
        self.zero_button.pack(fill=tk.X, pady=(8, 0))

    def _build_workspace(self, parent):
        metric_row = ttk.Frame(parent, style="Card.TFrame")
        metric_row.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        metric_row.columnconfigure(0, weight=1)
        ttk.Label(metric_row, text="LIVE FORCE", style="Label.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.force_label = ttk.Label(
            metric_row, text="0.0000 N", style="Force.TLabel"
        )
        self.force_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.run_state_label = ttk.Label(
            metric_row, text="READY", style="Status.TLabel"
        )
        self.run_state_label.grid(row=0, column=1, rowspan=2, sticky="e")

        self._build_test_panel(parent)

        graph_host = ttk.Frame(parent, style="Card2.TFrame", padding=12)
        graph_host.grid(row=2, column=0, sticky="nsew")
        self.figure = Figure(figsize=(7, 5), dpi=100, facecolor=COLORS["surface_2"])
        self.axes = self.figure.add_subplot(111)
        self._style_graph()
        (self.line,) = self.axes.plot([], [], color=COLORS["graph"], linewidth=2)
        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_host)
        self.canvas.draw()
        self.canvas.get_tk_widget().configure(
            bg=COLORS["surface_2"], highlightthickness=0
        )
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _build_test_panel(self, parent):
        panel = ttk.Frame(parent, style="Card.TFrame", padding=12)
        panel.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        for column in range(7):
            panel.columnconfigure(column, weight=1 if column in {0, 5} else 0)

        self.mode_var = tk.StringVar(value="COMPRESSION")
        mode_box = ttk.Frame(panel, style="Card.TFrame")
        mode_box.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        ttk.Radiobutton(
            mode_box,
            text="압축 강도",
            variable=self.mode_var,
            value="COMPRESSION",
            style="Mode.TRadiobutton",
            command=self.on_mode_change,
        ).pack(anchor="w")
        ttk.Radiobutton(
            mode_box,
            text="Poroelasticity",
            variable=self.mode_var,
            value="PORO",
            style="Mode.TRadiobutton",
            command=self.on_mode_change,
        ).pack(anchor="w")

        self.distance_var = tk.StringVar(value="5.0")
        self.speed_var = tk.StringVar(value="0.5")
        self.filename_var = tk.StringVar(value="Sample_01")
        self.save_dir_var = tk.StringVar(value=self.settings.save_directory)

        fields = (
            ("Distance (mm)", self.distance_var, 1, 9),
            ("Speed (mm/s)", self.speed_var, 2, 9),
            ("File name", self.filename_var, 3, 13),
        )
        for label, variable, column, width in fields:
            ttk.Label(panel, text=label, style="Label.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0, 10)
            )
            ttk.Entry(panel, textvariable=variable, width=width).grid(
                row=1, column=column, sticky="ew", padx=(0, 10)
            )

        ttk.Label(panel, text="Save directory", style="Label.TLabel").grid(
            row=0, column=4, sticky="w"
        )
        directory_box = ttk.Frame(panel, style="Card.TFrame")
        directory_box.grid(row=1, column=4, columnspan=2, sticky="ew", padx=(0, 10))
        directory_box.columnconfigure(0, weight=1)
        self.save_dir_entry = ttk.Entry(
            directory_box, textvariable=self.save_dir_var, width=18
        )
        self.save_dir_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            directory_box,
            text="폴더",
            style="Secondary.TButton",
            command=self.select_directory,
        ).grid(row=0, column=1, padx=(6, 0))

        action_box = ttk.Frame(panel, style="Card.TFrame")
        action_box.grid(row=0, column=6, rowspan=2, sticky="e")
        self.start_button = ttk.Button(
            action_box,
            text="측정 시작",
            style="Accent.TButton",
            command=self.start_test,
        )
        self.start_button.pack(fill=tk.X, pady=(0, 4))
        self.stop_button = ttk.Button(
            action_box,
            text="정지 · 기록 종료",
            style="Danger.TButton",
            command=self.stop_test,
        )
        self.stop_button.pack(fill=tk.X)

    def _style_graph(self):
        self.axes.set_facecolor(COLORS["surface_2"])
        self.axes.set_title(
            "Compressive strength", color=COLORS["text"], loc="left", pad=16
        )
        self.axes.set_xlabel("Distance (mm)", color=COLORS["muted"])
        self.axes.set_ylabel("Force (N)", color=COLORS["muted"])
        self.axes.tick_params(colors=COLORS["muted"])
        for spine in self.axes.spines.values():
            spine.set_color(COLORS["border"])
        self.axes.grid(color=COLORS["border"], alpha=0.65, linewidth=0.8)
        self.axes.set_xlim(0, 5.25)
        self.axes.set_ylim(-0.01, 0.5)
        self.axes.margins(x=0)

    def _bind_keys(self):
        self.root.bind("<Up>", lambda event: self.on_key_press("JD", event))
        self.root.bind("<KeyRelease-Up>", self.on_key_release)
        self.root.bind("<Down>", lambda event: self.on_key_press("JU", event))
        self.root.bind("<KeyRelease-Down>", self.on_key_release)
        self.distance_var.trace_add("write", lambda *_args: self.update_graph_limits())

    def _theme_button_text(self):
        return "밝은 모드" if self.theme_name == "dark" else "어두운 모드"

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        COLORS.clear()
        COLORS.update(
            DARK_COLORS if self.theme_name == "dark" else LIGHT_COLORS
        )
        self.root.configure(bg=COLORS["bg"])
        self._configure_styles()
        self._style_graph()
        self.line.set_color(COLORS["graph"])
        self.figure.set_facecolor(COLORS["surface_2"])
        self.canvas.get_tk_widget().configure(bg=COLORS["surface_2"])
        self.canvas.draw_idle()
        self.theme_button.config(text=self._theme_button_text())
        self._save_current_settings()

    def _notify(self, kind, title, text):
        if not self.show_dialogs:
            return
        getattr(messagebox, kind)(title, text, parent=self.root)

    def save_defaults(self):
        try:
            path = self._save_current_settings()
            self.status_label.config(text="●  기본값 저장됨")
            self._notify("showinfo", "기본값 저장", f"설정을 저장했습니다.\n{path}")
        except (OSError, ValueError) as exc:
            self._notify("showerror", "저장 오류", str(exc))

    def _save_current_settings(self):
        settings = AppSettings(
            motor_port=self.motor_port_var.get().strip().upper(),
            indicator_port=self.indicator_port_var.get().strip().upper(),
            save_directory=self.save_dir_var.get().strip(),
            theme=self.theme_name,
        )
        path = save_settings(settings, self.config_path)
        self.settings = settings
        return path

    def connect_devices(self):
        try:
            if self.demo:
                self.arduino = MemorySerial("DEMO_MOTOR")
                self.indicator = MemorySerial("DEMO_LOADCELL")
            else:
                self.arduino = self.serial_factory(
                    self.motor_port_var.get().strip().upper(),
                    MOTOR_BAUDRATE,
                    timeout=0.1,
                )
                self.indicator = self.serial_factory(
                    self.indicator_port_var.get().strip().upper(),
                    INDICATOR_BAUDRATE,
                    timeout=0.1,
                )
                threading.Thread(
                    target=self._read_indicator_thread, daemon=True
                ).start()
            self.status_label.config(
                text="●  DEMO 연결" if self.demo else "●  장비 연결됨"
            )
            self.connect_button.config(text="연결됨", state=tk.DISABLED)
        except Exception as exc:
            self.disconnect_devices()
            self._notify("showerror", "연결 오류", str(exc))

    def disconnect_devices(self):
        for device in (self.arduino, self.indicator):
            if device and getattr(device, "is_open", False):
                device.close()
        self.arduino = None
        self.indicator = None
        if hasattr(self, "status_label"):
            self.status_label.config(text="●  연결 안 됨")

    def _read_indicator_thread(self):
        while self.indicator and self.indicator.is_open:
            try:
                line = self.indicator.readline().decode(
                    "utf-8", errors="ignore"
                ).strip()
                if line:
                    self.indicator_queue.put(line)
            except Exception:
                break

    def _process_indicator_queue(self):
        if self.closing:
            return
        while True:
            try:
                self._handle_indicator_line(self.indicator_queue.get_nowait())
            except queue.Empty:
                break
        self.indicator_after_id = self.root.after(
            50, self._process_indicator_queue
        )

    def _handle_indicator_line(self, line):
        match = re.search(r"([-+]\s*\d+\.?\d*)", line)
        if not match:
            return
        value = float(match.group(1).replace(" ", ""))
        if abs(value) > 50000:
            return
        self.current_weight_g = -value
        raw_force = (self.current_weight_g / 1000.0) * 9.80665
        net_force = raw_force - self.force_offset_N
        self.force_label.config(text=f"{net_force:.4f} N")
        if self.is_running:
            self._record_sample(net_force)

    def _record_sample(self, force):
        elapsed = time.time() - self.start_time
        if not self.motor_reached_target:
            distance = (time.time() - self.start_motor_time) * self.current_speed
            if distance >= self.target_dist:
                distance = self.target_dist
                self.motor_reached_target = True
                self.send_stop()
                if self.mode_var.get() == "COMPRESSION":
                    self.is_running = False
        else:
            distance = self.target_dist

        self.time_data.append(elapsed)
        self.dist_data.append(distance)
        self.force_data.append(force)
        if self.csv_writer:
            self.csv_writer.writerow(
                [round(elapsed, 3), round(distance, 4), round(force, 4)]
            )

    def select_directory(self):
        selected = filedialog.askdirectory(
            title="데이터 저장 폴더 선택", parent=self.root
        )
        if selected:
            self.save_dir_var.set(selected)

    def zero_force(self):
        self.force_offset_N = (self.current_weight_g / 1000.0) * 9.80665
        self.force_label.config(text="0.0000 N")

    def on_key_press(self, direction, event):
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
            self.key_release_timer = None
        if self.current_jog_dir != direction:
            self.current_jog_dir = direction
            self.start_jog(direction)

    def on_key_release(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
        self.key_release_timer = self.root.after(50, self.stop_jog)

    def start_jog(self, direction):
        if not self._motor_ready():
            return
        try:
            self.arduino.write(jog_command(direction, self.jog_speed_var.get()))
            self.run_state_label.config(
                text="JOG DOWN" if direction == "JU" else "JOG UP"
            )
        except ValueError as exc:
            self._notify("showerror", "입력 오류", str(exc))

    def stop_jog(self):
        self.current_jog_dir = None
        self.send_stop()
        self.run_state_label.config(text="READY")

    def send_stop(self):
        if self._motor_ready():
            self.arduino.write(stop_command())

    def start_test(self):
        if not self._motor_ready():
            self._notify("showwarning", "연결 필요", "장비를 먼저 연결하세요.")
            return
        try:
            self.target_dist = float(self.distance_var.get())
            self.current_speed = float(self.speed_var.get())
            command = start_command(self.target_dist, self.current_speed)
            self._open_csv()
        except (OSError, ValueError) as exc:
            self._notify("showerror", "시작 오류", str(exc))
            return

        self.time_data.clear()
        self.dist_data.clear()
        self.force_data.clear()
        self.line.set_data([], [])
        self.motor_reached_target = False
        self.start_time = time.time()
        self.start_motor_time = self.start_time
        self.is_running = True
        self.arduino.write(command)
        self.run_state_label.config(text="RUNNING")
        self.update_loop()

    def _open_csv(self):
        self._close_csv()
        base_name = self.filename_var.get().strip()
        if not base_name:
            base_name = (
                "Compress"
                if self.mode_var.get() == "COMPRESSION"
                else "Poroelasticity"
            )
        filename = f"{base_name}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        save_dir = self.save_dir_var.get().strip()
        path = Path(save_dir) / filename if save_dir else Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_filename = str(path)
        self.csv_file = path.open("w", newline="", encoding="utf-8-sig")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(["Time (s)", "Distance (mm)", "Force (N)"])

    def stop_test(self):
        self.is_running = False
        self.send_stop()
        self._close_csv()
        self.run_state_label.config(text="STOPPED")

    def _close_csv(self):
        if self.csv_file and not self.csv_file.closed:
            self.csv_file.close()
        self.csv_file = None
        self.csv_writer = None

    def _motor_ready(self):
        return bool(self.arduino and getattr(self.arduino, "is_open", False))

    def on_mode_change(self):
        if self.mode_var.get() == "COMPRESSION":
            self.axes.set_title(
                "Compressive strength", color=COLORS["text"], loc="left", pad=16
            )
            self.axes.set_xlabel("Distance (mm)", color=COLORS["muted"])
        else:
            self.axes.set_title(
                "Poroelasticity / stress relaxation",
                color=COLORS["text"],
                loc="left",
                pad=16,
            )
            self.axes.set_xlabel("Time (s)", color=COLORS["muted"])
        self.time_data.clear()
        self.dist_data.clear()
        self.force_data.clear()
        self.line.set_data([], [])
        self.update_graph_limits()

    def update_graph_limits(self):
        if self.is_running:
            return
        if self.mode_var.get() == "COMPRESSION":
            try:
                distance = float(self.distance_var.get())
                self.axes.set_xlim(0, distance * 1.05 if distance > 0 else 5.0)
            except ValueError:
                pass
        else:
            self.axes.set_xlim(0, 60.0)
        self.canvas.draw_idle()

    def update_loop(self):
        if not self.is_running:
            return
        length = min(
            len(self.time_data), len(self.dist_data), len(self.force_data)
        )
        if length:
            x_data = (
                self.dist_data[:length]
                if self.mode_var.get() == "COMPRESSION"
                else self.time_data[:length]
            )
            force_data = self.force_data[:length]
            self.line.set_data(x_data, force_data)
            min_force = min(force_data)
            max_force = max(force_data)
            current_min, current_max = self.axes.get_ylim()
            if max_force > current_max or min_force < current_min:
                self.axes.set_ylim(min_force - 0.05, max_force + 0.1)
            self.canvas.draw_idle()
        self.update_after_id = self.root.after(50, self.update_loop)

    def close(self):
        self.closing = True
        for after_id in (self.indicator_after_id, self.update_after_id):
            if after_id:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
        self.stop_test()
        self.disconnect_devices()
        self.root.destroy()


def parse_args():
    parser = argparse.ArgumentParser(description="Hydrogel Compression Studio v2")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="하드웨어 없이 메모리 시리얼로 UI를 실행합니다.",
    )
    parser.add_argument(
        "--theme",
        choices=("dark", "light"),
        help="저장된 설정 대신 지정한 화면 테마로 시작합니다.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = tk.Tk()
    HydrogelTesterApp(root, demo=args.demo, initial_theme=args.theme)
    root.mainloop()
