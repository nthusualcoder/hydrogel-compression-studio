import os
import tkinter as tk
from tkinter import messagebox, filedialog
import serial
import threading
import time
import csv
import json
from datetime import datetime
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# --- 눈이 편안한 단일 밝은 테마 설정 (Warm Sand & indigo) ---
THEME = {
    "bg": "#f8f9fa",           # 메인 윈도우 배경 (부드러운 크림 그레이)
    "card_bg": "#ffffff",      # 카드 프레임 내부 배경 (완전 백색)
    "fg": "#212529",           # 기본 글자색 (가독성 높은 어두운 차콜)
    "fg_sub": "#6c757d",       # 보조 설명 글자색 (중간 회색)
    "accent": "#4f46e5",       # 액센트 포인트 컬러 (차분한 인디고 블루)
    "success": "#059669",      # 시작/연결 성공색 (차분한 포레스트 그린)
    "danger": "#dc2626",       # 중지/비상 정지색 (클래식 레드)
    "warning": "#d97706",      # 주의/경고색 (오렌지 브라운)
    "entry_bg": "#ffffff",     # 입력 필드 배경
    "entry_fg": "#212529",     # 입력 필드 텍스트
    "border": "#dee2e6"        # 가벼운 테두리 선 색상
}

CONFIG_FILE = "config.json"


class RoundedButton(tk.Canvas):
    """Tkinter에서 4개의 꼭짓점을 부드럽고 둥글게 깎아 디자인을 완성하는 커스텀 캔버스 버튼"""
    def __init__(self, parent, text, command=None, bg="#e9ecef", fg="#212529", active_bg="#dee2e6", active_fg="#212529", radius=8, height=32, **kwargs):
        # parent의 배경색을 그대로 가져와 테두리 밖을 투명하게 처리함
        super().__init__(parent, height=height, bg=parent["bg"], highlightthickness=0, bd=0)
        self.command = command
        self.bg = bg
        self.fg = fg
        self.active_bg = active_bg
        self.active_fg = active_fg
        self.radius = radius
        self.height = height
        self.text = text
        self.is_disabled = False
        self.width = 100  # 리사이즈 이벤트 수신 전 초기 가로 폭
        
        # 이벤트 바인딩
        self.bind("<Configure>", self.on_resize)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)
        self.bind("<ButtonRelease-1>", self.on_release)
        
    def config(self, **kwargs):
        if "state" in kwargs:
            state = kwargs["state"]
            if state == tk.DISABLED or state == "disabled":
                self.is_disabled = True
                self.draw_button("#e2e8f0", "#94a3b8")
            else:
                self.is_disabled = False
                self.draw_button(self.bg, self.fg)
        if "command" in kwargs:
            self.command = kwargs["command"]
        if "text" in kwargs:
            self.text = kwargs["text"]
            self.draw_button(self.bg if not self.is_disabled else "#e2e8f0", self.fg if not self.is_disabled else "#94a3b8")
            
    def configure(self, **kwargs):
        self.config(**kwargs)

    def on_resize(self, event):
        self.width = event.width
        self.draw_button(self.bg if not self.is_disabled else "#e2e8f0", self.fg if not self.is_disabled else "#94a3b8")
        
    def draw_button(self, bg_color, fg_color):
        self.delete("all")
        r = self.radius
        w = self.width
        h = self.height
        
        # 4개의 꼭짓점 둥근 사각형 그리기
        self.create_rounded_rectangle(0, 0, w, h, r, fill=bg_color, outline=bg_color)
        
        # 중앙 텍스트 그리기
        self.create_text(w/2, h/2, text=self.text, fill=fg_color, font=("Helvetica", 9, "bold"))
        
    def create_rounded_rectangle(self, x1, y1, x2, y2, r, **kwargs):
        d = r * 2
        # 네 모퉁이에 원 그리기
        self.create_oval(x1, y1, x1+d, y1+d, **kwargs)
        self.create_oval(x2-d, y1, x2, y1+d, **kwargs)
        self.create_oval(x1, y2-d, x1+d, y2, **kwargs)
        self.create_oval(x2-d, y2-d, x2, y2, **kwargs)
        
        # 상하좌우를 메우는 사각형 그리기
        self.create_rectangle(x1+r, y1, x2-r, y2, **kwargs)
        self.create_rectangle(x1, y1+r, x2, y2-r, **kwargs)
        
    def on_enter(self, event):
        if not self.is_disabled:
            self.draw_button(self.active_bg, self.active_fg)
            
    def on_leave(self, event):
        if not self.is_disabled:
            self.draw_button(self.bg, self.fg)
            
    def on_click(self, event):
        if not self.is_disabled:
            self.draw_button(self.active_bg, self.active_fg)
            
    def on_release(self, event):
        if not self.is_disabled:
            self.draw_button(self.bg, self.fg)
            if self.command:
                self.command()


class HydrogelTesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Hydrogel Tester v2 (Mega2560 + RAMPS 1.4)")
        self.root.geometry("1120x800")
        self.root.minsize(1080, 760)

        # 상태 변수
        self.arduino = None
        self.indicator = None
        self.is_running = False
        self.motor_reached_target = False
        self.arduino_thread_active = False
        self.indicator_thread_active = False

        self.current_weight_kg = 0.0
        self.force_offset_N = 0.0  
        self.start_time = 0.0
        self.start_motor_time = 0.0
        self.current_dist = 0.0
        self.target_dist = 0.0
        self.current_speed = 0.0
        
        self.current_jog_dir = None
        self.key_release_timer = None
        self.time_data, self.dist_data, self.force_data = [], [], []
        self.last_draw_time = 0.0
        
        self.save_dir = os.getcwd() 

        # 설정 로드 및 테마 초기화
        self.load_config()
        self.root.configure(bg=THEME["bg"])

        self.setup_ui()
        self.update_graph_limits()

        # 키보드 이벤트 바인딩
        self.root.bind('<Button-1>', self.clear_focus)
        self.root.bind('<KeyPress-Left>', lambda e: self.on_key_press("JF", e))   
        self.root.bind('<KeyRelease-Left>', self.on_key_release)
        self.root.bind('<KeyPress-Right>', lambda e: self.on_key_press("JB", e)) 
        self.root.bind('<KeyRelease-Right>', self.on_key_release)

    def load_config(self):
        """설정 파일(config.json)을 읽어 포트 기본값을 설정합니다."""
        self.default_arduino = "COM3"
        self.default_indicator = "COM4"
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    self.default_arduino = config.get("arduino_port", "COM3")
                    self.default_indicator = config.get("indicator_port", "COM4")
            except Exception as e:
                print(f"설정 파일 로드 실패: {e}")

    def save_config(self):
        """현재 입력된 포트 번호를 config.json에 영구 저장합니다."""
        config = {
            "arduino_port": self.ent_arduino.get(),
            "indicator_port": self.ent_indicator.get()
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            messagebox.showinfo("저장 완료", f"기본 포트 정보가 저장되었습니다.\nArduino: {config['arduino_port']}\nLoadcell: {config['indicator_port']}")
        except Exception as e:
            messagebox.showerror("저장 오류", f"설정을 저장하지 못했습니다: {e}")

    def setup_ui(self):
        # 헤더 프레임
        header_frame = tk.Frame(self.root, bg=THEME["bg"], height=50)
        header_frame.pack(fill=tk.X, padx=15, pady=(15, 5))
        
        lbl_title = tk.Label(header_frame, text="Hydrogel Compression Tester", font=("Helvetica", 16, "bold"), bg=THEME["bg"], fg=THEME["fg"])
        lbl_title.pack(side=tk.LEFT)
        
        # 메인 컨테이너 프레임
        main_frame = tk.Frame(self.root, bg=THEME["bg"])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        # 왼쪽 제어부 (가로폭을 360 -> 390으로 확장하여 내부 위젯 이탈 완벽 방지)
        self.control_frame = tk.Frame(main_frame, width=390, bg=THEME["bg"])
        self.control_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        self.control_frame.pack_propagate(False)

        # 오른쪽 그래프부
        self.graph_frame = tk.Frame(main_frame, bg=THEME["bg"])
        self.graph_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # ---------------------------------------------
        # [Card 1] 포트 및 통신 설정
        # ---------------------------------------------
        card_conn = tk.LabelFrame(self.control_frame, text=" 1. 포트 설정 ", font=("Helvetica", 10, "bold"), padx=15, pady=12, bg=THEME["card_bg"], fg=THEME["accent"], relief=tk.GROOVE, bd=1)
        card_conn.pack(fill=tk.X, pady=(0, 12))

        p_frame = tk.Frame(card_conn, bg=THEME["card_bg"])
        p_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(p_frame, text="Mega:", bg=THEME["card_bg"], fg=THEME["fg"]).pack(side=tk.LEFT)
        self.ent_arduino = tk.Entry(p_frame, width=7, relief=tk.SOLID, bd=1, bg=THEME["entry_bg"], fg=THEME["entry_fg"], insertbackground=THEME["fg"])
        self.ent_arduino.insert(0, self.default_arduino)
        self.ent_arduino.pack(side=tk.LEFT, padx=5)
        
        tk.Label(p_frame, text="Loadcell:", bg=THEME["card_bg"], fg=THEME["fg"]).pack(side=tk.LEFT)
        self.ent_indicator = tk.Entry(p_frame, width=7, relief=tk.SOLID, bd=1, bg=THEME["entry_bg"], fg=THEME["entry_fg"], insertbackground=THEME["fg"])
        self.ent_indicator.insert(0, self.default_indicator)
        self.ent_indicator.pack(side=tk.LEFT, padx=5)

        btn_save = RoundedButton(p_frame, text="💾 저장", command=self.save_config, bg="#e9ecef", fg=THEME["fg"], active_bg="#dee2e6")
        btn_save.pack(side=tk.RIGHT, padx=(5, 0))

        # 연결/해제 버튼 박스
        btn_box = tk.Frame(card_conn, bg=THEME["card_bg"])
        btn_box.pack(fill=tk.X, pady=(8, 0))

        self.btn_conn = RoundedButton(btn_box, text="🔌 장비 연결", command=self.connect_devices, bg=THEME["success"], fg="#ffffff", active_bg="#047857", height=35)
        self.btn_conn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_disconn = RoundedButton(btn_box, text="❌ 연결 해제", command=self.disconnect_devices, bg=THEME["danger"], fg="#ffffff", active_bg="#b91c1c", height=35)
        self.btn_disconn.config(state=tk.DISABLED)
        self.btn_disconn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        # ---------------------------------------------
        # [Card 2] 수동 제어 및 영점
        # ---------------------------------------------
        card_manual = tk.LabelFrame(self.control_frame, text=" 2. 수동 제어 및 영점 (방향키 ←, →) ", font=("Helvetica", 10, "bold"), padx=15, pady=12, bg=THEME["card_bg"], fg=THEME["accent"], relief=tk.GROOVE, bd=1)
        card_manual.pack(fill=tk.X, pady=(0, 12))

        js_frame = tk.Frame(card_manual, bg=THEME["card_bg"])
        js_frame.pack(fill=tk.X, pady=5)
        tk.Label(js_frame, text="이동 속도(mm/s):", bg=THEME["card_bg"], fg=THEME["fg"]).pack(side=tk.LEFT)
        self.ent_jog_speed = tk.Entry(js_frame, width=8, relief=tk.SOLID, bd=1, bg=THEME["entry_bg"], fg=THEME["entry_fg"], insertbackground=THEME["fg"])
        self.ent_jog_speed.insert(0, "0.5")
        self.ent_jog_speed.pack(side=tk.RIGHT)
        
        jog_frame = tk.Frame(card_manual, bg=THEME["card_bg"])
        jog_frame.pack(fill=tk.X, pady=10)
        
        self.btn_fw = RoundedButton(jog_frame, text="◀ 전진", bg="#fffbeb", fg="#b45309", active_bg="#fef3c7", height=36)
        self.btn_fw.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        self.btn_fw.bind('<ButtonPress-1>', lambda e: self.send_jog("JF"))
        self.btn_fw.bind('<ButtonRelease-1>', lambda e: self.send_stop())
        
        self.btn_bw = RoundedButton(jog_frame, text="▶ 후진", bg="#fffbeb", fg="#b45309", active_bg="#fef3c7", height=36)
        self.btn_bw.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        self.btn_bw.bind('<ButtonPress-1>', lambda e: self.send_jog("JB"))
        self.btn_bw.bind('<ButtonRelease-1>', lambda e: self.send_stop())
        
        self.btn_zero = RoundedButton(jog_frame, text="⚖️ 영점", command=self.zero_force, bg="#e9ecef", fg=THEME["fg"], active_bg="#dee2e6", height=36)
        self.btn_zero.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        # ---------------------------------------------
        # [Card 3] 자동 측정 설정 및 제어
        # ---------------------------------------------
        card_auto = tk.LabelFrame(self.control_frame, text=" 3. 자동 측정 설정 ", font=("Helvetica", 10, "bold"), padx=15, pady=12, bg=THEME["card_bg"], fg=THEME["accent"], relief=tk.GROOVE, bd=1)
        card_auto.pack(fill=tk.X, pady=(0, 12))

        # 측정 모드 라디오버튼
        mode_box = tk.Frame(card_auto, bg=THEME["card_bg"])
        mode_box.pack(fill=tk.X, pady=5)
        self.mode_var = tk.StringVar(value="COMPRESSION")
        
        self.rbtn_comp = tk.Radiobutton(mode_box, text="압축 강도 (X:거리)", variable=self.mode_var, value="COMPRESSION", command=self.update_graph_limits, bg=THEME["card_bg"], fg=THEME["fg"], selectcolor=THEME["entry_bg"], activebackground=THEME["card_bg"])
        self.rbtn_comp.pack(side=tk.LEFT, expand=True, anchor="w")
        
        self.rbtn_poro = tk.Radiobutton(mode_box, text="Poroelasticity (X:시간)", variable=self.mode_var, value="PORO", command=self.update_graph_limits, bg=THEME["card_bg"], fg=THEME["fg"], selectcolor=THEME["entry_bg"], activebackground=THEME["card_bg"])
        self.rbtn_poro.pack(side=tk.LEFT, expand=True, anchor="w")
        
        # 파일 저장 관리
        dir_frame = tk.Frame(card_auto, bg=THEME["card_bg"])
        dir_frame.pack(fill=tk.X, pady=5)
        btn_dir = RoundedButton(dir_frame, text="📁 저장 위치", command=self.browse_folder, bg="#e9ecef", fg=THEME["fg"], active_bg="#dee2e6")
        btn_dir.pack(side=tk.LEFT, padx=(0, 5))
        
        self.lbl_dir = tk.Label(dir_frame, text=self.save_dir, fg=THEME["fg_sub"], bg=THEME["card_bg"], anchor="w", font=("Helvetica", 8))
        self.lbl_dir.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.update_dir_label()

        f_frame = tk.Frame(card_auto, bg=THEME["card_bg"])
        f_frame.pack(fill=tk.X, pady=5)
        tk.Label(f_frame, text="파일명:", bg=THEME["card_bg"], fg=THEME["fg"]).pack(side=tk.LEFT)
        self.ent_fn = tk.Entry(f_frame, relief=tk.SOLID, bd=1, bg=THEME["entry_bg"], fg=THEME["entry_fg"], insertbackground=THEME["fg"])
        self.ent_fn.insert(0, "Sample_01")
        self.ent_fn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0))
        
        # 측정 거리 및 속도 (시험 속도 -> 이동 속도 수정 완료)
        ds_frame = tk.Frame(card_auto, bg=THEME["card_bg"])
        ds_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(ds_frame, text="목표 거리(mm):", bg=THEME["card_bg"], fg=THEME["fg"]).grid(row=0, column=0, sticky="w", pady=2)
        self.ent_dist = tk.Entry(ds_frame, width=9, relief=tk.SOLID, bd=1, bg=THEME["entry_bg"], fg=THEME["entry_fg"], insertbackground=THEME["fg"])
        self.ent_dist.insert(0, "5.0")
        self.ent_dist.grid(row=0, column=1, padx=(3, 8), pady=2)
        self.ent_dist.bind('<KeyRelease>', self.update_graph_limits)
        
        tk.Label(ds_frame, text="이동 속도(mm/s):", bg=THEME["card_bg"], fg=THEME["fg"]).grid(row=0, column=2, sticky="w", pady=2)
        self.ent_speed = tk.Entry(ds_frame, width=9, relief=tk.SOLID, bd=1, bg=THEME["entry_bg"], fg=THEME["entry_fg"], insertbackground=THEME["fg"])
        self.ent_speed.insert(0, "0.5")
        self.ent_speed.grid(row=0, column=3, padx=(3, 0), pady=2)

        # 시작/정지 버튼
        control_btn_box = tk.Frame(card_auto, bg=THEME["card_bg"])
        control_btn_box.pack(fill=tk.X, pady=(10, 0))

        self.btn_start = RoundedButton(control_btn_box, text="▶ TEST START", command=self.start_test, bg=THEME["success"], fg="#ffffff", active_bg="#047857", height=35)
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_stop = RoundedButton(control_btn_box, text="■ TEST STOP", command=self.stop_test, bg=THEME["danger"], fg="#ffffff", active_bg="#b91c1c", height=35)
        self.btn_stop.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        # ---------------------------------------------
        # [Card 4] 상태 모니터 영역 (박스 안에 완전히 격리 배치)
        # ---------------------------------------------
        card_monitor = tk.LabelFrame(self.control_frame, text=" 4. 상태 모니터 ", font=("Helvetica", 10, "bold"), padx=15, pady=12, bg=THEME["card_bg"], fg=THEME["accent"], relief=tk.GROOVE, bd=1)
        card_monitor.pack(fill=tk.BOTH, expand=True)

        self.lbl_force = tk.Label(card_monitor, text="0.0000 N", font=("Helvetica", 26, "bold"), fg=THEME["accent"], bg=THEME["card_bg"])
        self.lbl_force.pack(pady=(12, 6), fill=tk.X)

        self.lbl_status = tk.Label(card_monitor, text="장비 연결 대기 중", font=("Helvetica", 10), fg=THEME["fg_sub"], bg=THEME["card_bg"])
        self.lbl_status.pack(pady=5, fill=tk.X)

        # ---------------------------------------------
        # 오른쪽 그래프 구현 (Matplotlib)
        # ---------------------------------------------
        self.fig = Figure(figsize=(6, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Real-time Data Plot", color=THEME["fg"])
        self.ax.grid(True, color=THEME["border"], linestyle="--", alpha=0.5)
        
        self.line, = self.ax.plot([], [], color=THEME["accent"], linewidth=2)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 포커스 클리어 처리용 바인딩
        entries = [self.ent_arduino, self.ent_indicator, self.ent_jog_speed, self.ent_fn, self.ent_dist, self.ent_speed]
        for ent in entries:
            ent.bind('<Return>', lambda e: self.root.focus_set())

    def update_dir_label(self):
        """저장 위치 라벨의 글자 길이 제한 및 포맷 업데이트"""
        p = self.save_dir
        # 박스 범위를 절대 이탈하지 않도록 노출 길이를 22글자로 대폭 축소 조정
        display_path = p if len(p) < 22 else "..." + p[-19:]
        self.lbl_dir.config(text=display_path)

    def browse_folder(self):
        folder_selected = filedialog.askdirectory(initialdir=self.save_dir, title="데이터 저장 폴더 선택")
        if folder_selected: 
            self.save_dir = folder_selected
            self.update_dir_label()

    def clear_focus(self, event):
        if not isinstance(event.widget, tk.Entry):
            self.root.focus_set()

    def zero_force(self): 
        self.force_offset_N = self.current_weight_kg * 9.80665
        self.log_status("로드셀 영점이 조절되었습니다.", "normal")

    def update_graph_limits(self, e=None):
        if self.is_running: return
        mode = self.mode_var.get()
        
        self.ax.clear()
        self.ax.grid(True, color=THEME["border"], linestyle="--", alpha=0.5)
        self.ax.set_facecolor(THEME["card_bg"])
        self.fig.patch.set_facecolor(THEME["bg"])
        
        self.ax.spines['bottom'].set_color(THEME["fg_sub"])
        self.ax.spines['top'].set_color(THEME["fg_sub"])
        self.ax.spines['left'].set_color(THEME["fg_sub"])
        self.ax.spines['right'].set_color(THEME["fg_sub"])
        self.ax.tick_params(colors=THEME["fg_sub"])
        
        self.line, = self.ax.plot([], [], color=THEME["accent"], linewidth=2)
        
        if mode == "COMPRESSION":
            try: 
                d = float(self.ent_dist.get())
                self.ax.set_xlim(0, d*1.1)
            except ValueError:
                self.ax.set_xlim(0, 5.0)
            self.ax.set_xlabel("Distance (mm)", color=THEME["fg"])
        else: 
            self.ax.set_xlim(0, 60)
            self.ax.set_xlabel("Time (s)", color=THEME["fg"])
            
        self.ax.set_ylabel("Force (N)", color=THEME["fg"])
        self.ax.set_title("Ready" if not self.is_running else "Testing...", color=THEME["fg"], fontsize=12, fontweight='bold')
        self.canvas.draw()

    # ---------------------------------------------
    # 장비 연결 및 해제 로직
    # ---------------------------------------------
    def connect_devices(self):
        ard_port = self.ent_arduino.get().strip()
        ind_port = self.ent_indicator.get().strip()
        
        try:
            self.arduino = serial.Serial(ard_port, 115200, timeout=0.1)
            self.indicator = serial.Serial(ind_port, 115200, timeout=0.02)
            
            self.arduino_thread_active = True
            self.indicator_thread_active = True
            
            threading.Thread(target=self.read_arduino_thread, daemon=True).start()
            threading.Thread(target=self.read_indicator_thread, daemon=True).start()
            
            self.btn_conn.config(state=tk.DISABLED)
            self.btn_disconn.config(state=tk.NORMAL)
            self.log_status("장비 연결에 성공하였습니다.", "success")
            
        except Exception as e: 
            self.disconnect_devices()
            messagebox.showerror("연결 오류", f"장비 연결 중 에러가 발생했습니다:\n{e}")

    def disconnect_devices(self):
        self.is_running = False
        self.arduino_thread_active = False
        self.indicator_thread_active = False
        
        if self.arduino and self.arduino.is_open:
            try:
                self.arduino.write(b"STOP\n")
                time.sleep(0.05)
                self.arduino.close()
            except: pass
        self.arduino = None
        
        if self.indicator and self.indicator.is_open:
            try:
                self.indicator.close()
            except: pass
        self.indicator = None
        
        self.btn_conn.config(state=tk.NORMAL)
        self.btn_disconn.config(state=tk.DISABLED)
        self.log_status("장비 연결이 해제되었습니다.", "normal")

    # ---------------------------------------------
    # 백그라운드 시리얼 수신 스레드
    # ---------------------------------------------
    def read_arduino_thread(self):
        while self.arduino_thread_active and self.arduino and self.arduino.is_open:
            try:
                if self.arduino.in_waiting > 0:
                    line = self.arduino.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        print(f"[Arduino Rx] {line}")
                        if line == "DONE":
                            self.root.after_idle(self.on_test_done)
                        elif line == "STOP_BUTTON":
                            self.root.after_idle(self.on_emergency_stop)
                        elif line == "STOPPED":
                            self.log_status("모터 작동이 정지되었습니다.", "normal")
                else:
                    time.sleep(0.005)
            except:
                break

    def read_indicator_thread(self):
        while self.indicator_thread_active and self.indicator and self.indicator.is_open:
            try:
                if self.indicator.in_waiting > 0:
                    line = self.indicator.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        line_lower = line.lower()
                        if "kg" in line_lower or "g" in line_lower:
                            sign = -1 if "-" in line else 1
                            num = ''.join(c for c in line if c.isdigit() or c == '.')
                            if num:
                                raw_val = float(num)
                                if "kg" in line_lower:
                                    self.current_weight_kg = sign * raw_val
                                else:
                                    self.current_weight_kg = sign * (raw_val / 1000.0)
                                    
                                val = (self.current_weight_kg * 9.80665) - self.force_offset_N
                                self.root.after_idle(lambda v=val: self.lbl_force.config(text=f"{v:.4f} N"))
                else:
                    time.sleep(0.002)
            except:
                break

    # ---------------------------------------------
    # 키보드 / 마우스 조그 및 영점 이벤트
    # ---------------------------------------------
    def on_key_press(self, d, e):
        if isinstance(e.widget, tk.Entry): return
        if self.key_release_timer: 
            self.root.after_cancel(self.key_release_timer)
            self.key_release_timer = None
        if self.current_jog_dir != d: 
            self.current_jog_dir = d
            self.send_jog(d)

    def on_key_release(self, e):
        if isinstance(e.widget, tk.Entry): return
        if self.key_release_timer: 
            self.root.after_cancel(self.key_release_timer)
        self.key_release_timer = self.root.after(50, self.exec_release)

    def exec_release(self): 
        self.current_jog_dir = None
        self.send_stop()

    def send_jog(self, d):
        if self.arduino and self.arduino.is_open:
            try:
                cmd = f"{d},{self.ent_jog_speed.get()}\n"
                self.arduino.write(cmd.encode())
            except Exception as e:
                self.log_status(f"송신 오류: {e}", "danger")
        else:
            self.log_status("아두이노가 연결되어 있지 않습니다.", "warning")

    def send_stop(self):
        if self.arduino and self.arduino.is_open:
            try:
                self.arduino.write(b"STOP\n")
            except Exception as e:
                self.log_status(f"송신 오류: {e}", "danger")

    # ---------------------------------------------
    # 자동 압축 시험 시나리오 제어
    # ---------------------------------------------
    def start_test(self):
        if not self.arduino or not self.arduino.is_open:
            messagebox.showwarning("연결 확인", "아두이노가 연결되지 않았습니다.")
            return
            
        try:
            self.target_dist = float(self.ent_dist.get())
            self.current_speed = float(self.ent_speed.get())
            if self.target_dist <= 0 or self.current_speed <= 0:
                raise ValueError("거리와 속도는 0보다 커야 합니다.")
        except ValueError as err:
            messagebox.showwarning("입력값 오류", f"유효한 거리 및 속도를 입력하십시오.\n{err}")
            return

        self.time_data, self.dist_data, self.force_data = [], [], []
        self.update_graph_limits()
        
        # CSV 파일 설정
        fn = f"{self.ent_fn.get()}_{datetime.now().strftime('%H%M%S')}.csv"
        full_path = os.path.join(self.save_dir, fn)
        
        try:
            self.csv_file = open(full_path, 'w', newline='')
            self.writer = csv.writer(self.csv_file)
            self.writer.writerow(["Time", "Dist", "Force"])
        except Exception as e:
            messagebox.showerror("파일 오류", f"저장 파일을 생성할 수 없습니다:\n{e}")
            return

        # 아두이노 모터 기동 명령
        cmd = f"start,{self.target_dist},{self.current_speed}\n"
        self.arduino.write(cmd.encode())
        
        self.is_running = True
        self.motor_reached_target = False
        self.start_time = self.start_motor_time = time.time()
        self.last_draw_time = time.time()
        
        self.log_status("자동 시험 구동 중...", "success")
        self.update_loop()

    def stop_test(self):
        if self.is_running:
            self.is_running = False
            self.send_stop()
            self.log_status("시험이 수동으로 중지되었습니다.", "warning")
            if hasattr(self, 'csv_file') and not self.csv_file.closed:
                self.csv_file.close()

    def update_loop(self):
        if not self.is_running: 
            if hasattr(self, 'csv_file') and not self.csv_file.closed:
                self.csv_file.close()
            return
            
        now = time.time()
        dt = now - self.start_time
        
        if not self.motor_reached_target:
            d_now = (now - self.start_motor_time) * self.current_speed
            if d_now >= self.target_dist:
                d_now = self.target_dist
                self.motor_reached_target = True
                if d_now >= self.target_dist * 1.15:
                    self.log_status("시간 초과로 인한 안전 정지 작동", "danger")
                    self.stop_test()
                    return
        else: 
            d_now = self.target_dist
        
        f_net = (self.current_weight_kg * 9.80665) - self.force_offset_N
        
        self.time_data.append(dt)
        self.dist_data.append(d_now)
        self.force_data.append(f_net)
        
        try:
            self.writer.writerow([round(dt,3), round(d_now,4), round(f_net,4)])
        except: pass
        
        if now - self.last_draw_time > 0.1:
            if self.mode_var.get() == "COMPRESSION": 
                self.line.set_data(self.dist_data, self.force_data)
            else: 
                self.line.set_data(self.time_data, self.force_data)
            
            # 그래프 Y축 범위 동적 조절
            if self.force_data:
                min_f = min(self.force_data)
                max_f = max(self.force_data)
                
                y_min = -0.2
                y_max = 1.0
                
                if min_f < y_min:
                    y_min = min_f - 0.2
                if max_f > y_max:
                    y_max = max_f + 0.5
                    
                self.ax.set_ylim(y_min, y_max)
                
            if self.mode_var.get() == "PORO" and dt > self.ax.get_xlim()[1]:
                self.ax.set_xlim(0, dt + 10.0)
                
            self.canvas.draw()
            self.last_draw_time = now
        
        self.root.after(10, self.update_loop)

    def on_test_done(self):
        if self.is_running:
            self.is_running = False
            self.log_status("자동 시험이 완료되었습니다.", "success")
            if hasattr(self, 'csv_file') and not self.csv_file.closed:
                self.csv_file.close()
            
            if self.mode_var.get() == "COMPRESSION": 
                self.line.set_data(self.dist_data, self.force_data)
            else: 
                self.line.set_data(self.time_data, self.force_data)
            self.canvas.draw()
            
            messagebox.showinfo("시험 완료", "하이드로겔 압축 시험이 안전하게 완료되었습니다.")

    def on_emergency_stop(self):
        if self.is_running:
            self.is_running = False
            if hasattr(self, 'csv_file') and not self.csv_file.closed:
                self.csv_file.close()
        
        self.log_status("⚠️ 비상 정지(STOP BUTTON) 활성화됨", "danger")
        messagebox.showerror("비상 정지", "장비의 하드웨어 비상 정지 버튼이 작동되었습니다!\n안전을 위해 작동이 중지되었습니다.")

    def log_status(self, text, style_type="normal"):
        colors = {
            "normal": THEME["fg_sub"],
            "success": THEME["success"],
            "danger": THEME["danger"],
            "warning": THEME["warning"]
        }
        target_color = colors.get(style_type, THEME["fg"])
        self.lbl_status.config(text=text, fg=target_color)

if __name__ == "__main__":
    root = tk.Tk()
    app = HydrogelTesterApp(root)
    root.mainloop()
