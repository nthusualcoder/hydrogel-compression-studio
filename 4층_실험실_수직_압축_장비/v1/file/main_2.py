import tkinter as tk
from tkinter import ttk, messagebox
import serial
import threading
import time
import csv
from datetime import datetime

# 시작 전 생존 신고 1
print("▶ 1단계: 파이썬 기본 모듈 로딩 완료")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

print("▶ 2단계: 그래프(Matplotlib) 모듈 로딩 완료")

class HydrogelTesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Hydrogel Modulus & Poroelasticity Tester (Final)")
        self.root.geometry("1050x700") 

        self.arduino = None
        self.indicator = None
        self.is_running = False
        self.motor_reached_target = False
        
        self.current_weight_g = 0.0
        self.start_time = 0
        self.start_motor_time = 0
        self.target_dist = 0.0
        self.current_speed = 0.0
        self.csv_filename = ""
        
        # 방향키 타이머
        self.current_jog_dir = None
        self.key_release_timer = None
        
        self.time_data = []
        self.dist_data = []
        self.force_data = []

        print("▶ 3단계: UI 화면 그리기 시작...")
        self.setup_ui()
        print("▶ 4단계: UI 화면 그리기 완료!")

        # 키보드 방향키 바인딩
        self.root.bind('<KeyPress-Up>', lambda e: self.on_key_press("JD", e))   # 상승 
        self.root.bind('<KeyRelease-Up>', self.on_key_release)
        self.root.bind('<KeyPress-Down>', lambda e: self.on_key_press("JU", e)) # 하강 
        self.root.bind('<KeyRelease-Down>', self.on_key_release)

    def setup_ui(self):
        # --- 좌측: 제어 패널 ---
        control_frame = tk.Frame(self.root, width=340, padx=10, pady=10)
        control_frame.pack(side=tk.LEFT, fill=tk.Y)

        # 1. 포트 수동 연결
        tk.Label(control_frame, text="1. 포트 직접 입력", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,5))
        
        port_frame = tk.Frame(control_frame)
        port_frame.pack(fill=tk.X, pady=(0, 5))
        
        tk.Label(port_frame, text="Motor:").pack(side=tk.LEFT)
        self.ent_arduino = tk.Entry(port_frame, width=8)
        self.ent_arduino.insert(0, "COM10") 
        self.ent_arduino.pack(side=tk.LEFT, padx=(2, 10))
        
        tk.Label(port_frame, text="Loadcell:").pack(side=tk.LEFT)
        self.ent_indicator = tk.Entry(port_frame, width=8)
        self.ent_indicator.insert(0, "COM11") 
        self.ent_indicator.pack(side=tk.LEFT, padx=(2, 0))
        
        self.btn_connect = tk.Button(control_frame, text="장비 연결", command=self.connect_devices, bg="#d9edf7")
        self.btn_connect.pack(fill=tk.X, pady=(0,15))

        # 2. 수동 제어
        tk.Label(control_frame, text="2. 수동 제어 (키보드 ↑, ↓ 사용가능)", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,5))
        
        jog_speed_frame = tk.Frame(control_frame)
        jog_speed_frame.pack(fill=tk.X, pady=(0,5))
        tk.Label(jog_speed_frame, text="수동 이동 속도 (mm/s):").pack(side=tk.LEFT)
        self.ent_jog_speed = tk.Entry(jog_speed_frame, width=8)
        self.ent_jog_speed.insert(0, "0.5")
        self.ent_jog_speed.pack(side=tk.RIGHT)
        
        jog_frame = tk.Frame(control_frame)
        jog_frame.pack(fill=tk.X, pady=(0,15))
        
        self.btn_up = tk.Button(jog_frame, text="▼ 하강 (누르고 있기)", bg="#fcf8e3", height=2)
        self.btn_up.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,2))
        self.btn_up.bind('<ButtonPress-1>', lambda e: self.send_jog("JU"))
        self.btn_up.bind('<ButtonRelease-1>', lambda e: self.send_stop())

        self.btn_down = tk.Button(jog_frame, text="▲ 상승 (누르고 있기)", bg="#fcf8e3", height=2)
        self.btn_down.pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(2,0))
        self.btn_down.bind('<ButtonPress-1>', lambda e: self.send_jog("JD"))
        self.btn_down.bind('<ButtonRelease-1>', lambda e: self.send_stop())

        # 3. 실험 모드 선택 (듀얼 모드)
        tk.Label(control_frame, text="3. 측정 모드 선택", font=("Arial", 12, "bold"), fg="#8e44ad").pack(anchor="w", pady=(0,5))
        self.mode_var = tk.StringVar(value="COMPRESSION")
        
        rb1 = tk.Radiobutton(control_frame, text="압축 강도 측정 (X축: 거리)", variable=self.mode_var, value="COMPRESSION", command=self.on_mode_change)
        rb1.pack(anchor="w")
        rb2 = tk.Radiobutton(control_frame, text="Poroelasticity 측정 (X축: 시간)", variable=self.mode_var, value="PORO", command=self.on_mode_change)
        rb2.pack(anchor="w", pady=(0,10))

        # 4. 자동 압축 테스트
        tk.Label(control_frame, text="4. 자동 압축 시작", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,5))
        
        file_frame = tk.Frame(control_frame)
        file_frame.pack(fill=tk.X, pady=(0,5))
        tk.Label(file_frame, text="저장 파일명:").pack(side=tk.LEFT)
        self.ent_filename = tk.Entry(file_frame)
        self.ent_filename.insert(0, "Sample_01")
        self.ent_filename.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))

        dist_speed_frame = tk.Frame(control_frame)
        dist_speed_frame.pack(fill=tk.X, pady=(0,10))
        tk.Label(dist_speed_frame, text="거리(mm):").grid(row=0, column=0, sticky="w")
        self.ent_dist = tk.Entry(dist_speed_frame, width=8)
        self.ent_dist.insert(0, "5.0")
        self.ent_dist.grid(row=0, column=1, padx=(2,10))
        
        # ⭐ 거리를 입력할 때마다 실시간으로 X축 크기를 업데이트하도록 이벤트 바인딩
        self.ent_dist.bind('<KeyRelease>', self.update_graph_limits)
        
        tk.Label(dist_speed_frame, text="속도(mm/s):").grid(row=0, column=2, sticky="w")
        self.ent_speed = tk.Entry(dist_speed_frame, width=8)
        self.ent_speed.insert(0, "0.5")
        self.ent_speed.grid(row=0, column=3, padx=(2,0))

        self.btn_start = tk.Button(control_frame, text="▶ START (그래프 기록 시작)", command=self.start_test, bg="#dff0d8", font=("Arial", 10, "bold"), height=1)
        self.btn_start.pack(fill=tk.X, pady=(0,5))
        
        self.btn_stop = tk.Button(control_frame, text="■ 모터 정지 / 기록 종료 (STOP)", command=self.stop_test, bg="#f2dede", font=("Arial", 10, "bold"), height=1)
        self.btn_stop.pack(fill=tk.X, pady=(0,15))

        # 5. 실시간 하중
        tk.Label(control_frame, text="실시간 하중 (N):", font=("Arial", 10, "bold")).pack(anchor="w")
        self.lbl_force = tk.Label(control_frame, text="0.0000 N", font=("Arial", 16, "bold"), fg="blue")
        self.lbl_force.pack(anchor="w")

        # --- 우측: 그래프 패널 ---
        graph_frame = tk.Frame(self.root, padx=10, pady=10)
        graph_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Compressive Strength (Dist vs Force)")
        self.ax.set_xlabel("Distance (mm)")
        self.ax.set_ylabel("Force (N)")
        self.ax.grid(True)
        
        self.ax.set_xlim(0, 5.0) 
        self.ax.set_ylim(-0.2, 2.0)
        self.ax.margins(x=0) 
        
        self.line, = self.ax.plot([], [], 'b-', linewidth=2)
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # 시작 시 설정된 거리에 맞춰 초기 한 번 렌더링
        self.update_graph_limits()

    # --- 실시간 그래프 X축 자동 업데이트 로직 ---
    def update_graph_limits(self, event=None):
        if self.is_running: 
            return # 측정 중에는 사용자가 글자를 바꿔도 무시함
            
        mode = self.mode_var.get()
        if mode == "COMPRESSION":
            try:
                dist = float(self.ent_dist.get())
                x_limit = dist * 1.05 if dist > 0 else 5.0
                self.ax.set_xlim(0, x_limit)
            except ValueError:
                pass # 숫자가 아닌 값이 입력되면 무시
        else:
            self.ax.set_xlim(0, 60.0) # Poroelasticity 모드 기본 60초
            
        self.canvas.draw()

    # --- 키보드 방향키 제어 로직 ---
    def on_key_press(self, direction, event):
        if isinstance(event.widget, tk.Entry):
            return
            
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
            self.key_release_timer = None
            
        if self.current_jog_dir != direction:
            self.current_jog_dir = direction
            self.send_jog(direction)

    def on_key_release(self, event):
        if isinstance(event.widget, tk.Entry):
            return
            
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
        self.key_release_timer = self.root.after(50, self.execute_key_release)

    def execute_key_release(self):
        self.current_jog_dir = None
        self.send_stop()

    # --- 모드 변경 로직 ---
    def on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "COMPRESSION":
            self.ax.set_title("Compressive Strength (Dist vs Force)")
            self.ax.set_xlabel("Distance (mm)")
        else:
            self.ax.set_title("Poroelasticity / Stress Relaxation (Time vs Force)")
            self.ax.set_xlabel("Time (s)")
            
        self.ax.set_ylim(-0.2, 2.0)
        self.time_data.clear()
        self.dist_data.clear()
        self.force_data.clear()
        self.line.set_data([], [])
        
        # 모드를 바꿀 때에도 설정된 거리에 맞춰 X축 즉각 변경
        self.update_graph_limits()

    # --- 장비 제어 ---
    def connect_devices(self):
        port_arduino = self.ent_arduino.get().strip().upper()
        port_indicator = self.ent_indicator.get().strip().upper()
        
        try:
            self.arduino = serial.Serial(port_arduino, 115200, timeout=0.1)
            self.indicator = serial.Serial(port_indicator, 9600, timeout=0.1)
            threading.Thread(target=self.read_indicator_thread, daemon=True).start()
            self.btn_connect.config(text="연결 성공!", bg="#dff0d8", state="disabled")
            messagebox.showinfo("연결 완료", f"장비 연결 성공!")
        except Exception as e:
            messagebox.showerror("연결 오류", f"포트 접속 실패!\n{e}")

    def read_indicator_thread(self):
        while self.indicator and self.indicator.is_open:
            try:
                line = self.indicator.readline().decode('utf-8', errors='ignore').strip()
                if line and ("kg" in line or "g" in line):
                    sign = 1 if "-" in line else -1 
                    num_part = line[line.find('+') if '+' in line else line.find('-'):]
                    num_str = ''.join(c for c in num_part if c.isdigit() or c == '.')
                    if num_str:
                        self.current_weight_g = sign * float(num_str)
                        force_N = (self.current_weight_g / 1000.0) * 9.80665
                        self.lbl_force.config(text=f"{force_N:.4f} N")
            except: pass
            time.sleep(0.01)

    def send_jog(self, direction):
        if not self.arduino or not self.arduino.is_open: return
        speed = self.ent_jog_speed.get()
        self.arduino.write(f"{direction},{speed}\n".encode('utf-8'))

    def send_stop(self):
        if self.arduino and self.arduino.is_open:
            self.arduino.write(b"STOP\n")

    def stop_test(self):
        self.is_running = False
        self.send_stop()

    def start_test(self):
        if not self.arduino or not self.arduino.is_open:
            messagebox.showwarning("경고", "장비를 연결하세요.")
            return
            
        try:
            self.target_dist = float(self.ent_dist.get())
            self.current_speed = float(self.ent_speed.get())
        except ValueError:
            messagebox.showerror("오류", "거리와 속도에 숫자를 입력하세요.")
            return

        self.time_data.clear()
        self.dist_data.clear()
        self.force_data.clear()
        self.line.set_data([], [])
        
        # 시작 시점에 최종적으로 다시 한번 X축 갱신
        self.update_graph_limits()

        user_filename = self.ent_filename.get().strip()
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if not user_filename:
            user_filename = "Compress" if self.mode_var.get() == "COMPRESSION" else "Poroelasticity"
            
        self.csv_filename = f"{user_filename}_{now_str}.csv"
        
        with open(self.csv_filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Time (s)", "Distance (mm)", "Force (N)", "Raw Weight (g)"])

        cmd = f"start,{self.target_dist},{self.current_speed}\n"
        self.arduino.write(cmd.encode('utf-8'))
        
        self.is_running = True
        self.motor_reached_target = False
        self.start_time = time.time()
        self.start_motor_time = time.time()
        
        self.update_loop()

    def update_loop(self):
        if not self.is_running: return

        elapsed = time.time() - self.start_time
        mode = self.mode_var.get()

        if not self.motor_reached_target:
            motor_elapsed = time.time() - self.start_motor_time
            dist_now = motor_elapsed * self.current_speed
            
            if dist_now >= self.target_dist:
                dist_now = self.target_dist
                self.motor_reached_target = True
                self.send_stop()
                
                if mode == "COMPRESSION":
                    self.is_running = False
        else:
            dist_now = self.target_dist 

        force_N = (self.current_weight_g / 1000.0) * 9.80665

        self.time_data.append(elapsed)
        self.dist_data.append(dist_now)
        self.force_data.append(force_N)

        with open(self.csv_filename, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([round(elapsed, 3), round(dist_now, 4), round(force_N, 4), self.current_weight_g])

        if mode == "COMPRESSION":
            self.line.set_data(self.dist_data, self.force_data)
            # ⭐ 혹시라도 모터가 초과 이동했을 때 그래프가 잘리지 않도록 안전 스케일링
            if dist_now > self.ax.get_xlim()[1]:
                self.ax.set_xlim(0, dist_now * 1.05)
        else:
            self.line.set_data(self.time_data, self.force_data)
            if elapsed > self.ax.get_xlim()[1]:
                self.ax.set_xlim(0, elapsed * 1.2)
        
        if len(self.force_data) > 0:
            max_f = max(self.force_data)
            min_f = min(self.force_data)
            if max_f > self.ax.get_ylim()[1] or min_f < self.ax.get_ylim()[0]:
                self.ax.set_ylim(min_f - 0.2, max_f + 2)

        self.canvas.draw()

        if self.is_running:
            self.root.after(50, self.update_loop)

if __name__ == "__main__":
    print("▶ 0단계: 프로그램 시작 명령어 감지됨!")
    root = tk.Tk()
    app = HydrogelTesterApp(root)
    print("▶ 5단계: 정상적으로 프로그램이 실행되었습니다. 화면을 확인하세요!")
    root.mainloop()