import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import threading
import time
import csv
import re  
import os
from datetime import datetime

print("▶ 1단계: 파이썬 기본 모듈 로딩 완료")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

print("▶ 2단계: 그래프(Matplotlib) 모듈 로딩 완료")

class HydrogelTesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Hydrogel Modulus & Poroelasticity Tester (Arduino Uno)")
        self.root.geometry("1050x740") # UI가 살짝 늘어나서 창 크기를 조금 키웠습니다.

        self.arduino = None
        self.indicator = None
        self.is_running = False
        self.motor_reached_target = False
        
        self.current_weight_g = 0.0
        self.force_offset_N = 0.0  
        
        self.start_time = 0
        self.start_motor_time = 0
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

        self.setup_ui()

        self.root.bind('<Button-1>', self.clear_focus)

        self.root.bind('<Up>', lambda e: self.on_key_press("JD", e))   
        self.root.bind('<KeyRelease-Up>', self.on_key_release)
        self.root.bind('<Down>', lambda e: self.on_key_press("JU", e)) 
        self.root.bind('<KeyRelease-Down>', self.on_key_release)

    def setup_ui(self):
        control_frame = tk.Frame(self.root, width=340, padx=10, pady=10)
        control_frame.pack(side=tk.LEFT, fill=tk.Y)

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

        tk.Label(control_frame, text="2. 수동 제어 및 영점 (키보드 ↑, ↓ 사용)", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,5))
        
        jog_speed_frame = tk.Frame(control_frame)
        jog_speed_frame.pack(fill=tk.X, pady=(0,5))
        tk.Label(jog_speed_frame, text="수동 이동 속도 (mm/s):").pack(side=tk.LEFT)
        self.ent_jog_speed = tk.Entry(jog_speed_frame, width=8)
        self.ent_jog_speed.insert(0, "0.5")
        self.ent_jog_speed.pack(side=tk.RIGHT)
        
        jog_frame = tk.Frame(control_frame)
        jog_frame.pack(fill=tk.X, pady=(0,15))
        
        self.btn_down = tk.Button(jog_frame, text="▼ 하강", bg="#fcf8e3", height=2)
        self.btn_down.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,2))
        self.btn_down.bind('<ButtonPress-1>', lambda e: self.send_jog("JU"))
        self.btn_down.bind('<ButtonRelease-1>', lambda e: self.send_stop())

        self.btn_up = tk.Button(jog_frame, text="▲ 상승", bg="#fcf8e3", height=2)
        self.btn_up.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2,2))
        self.btn_up.bind('<ButtonPress-1>', lambda e: self.send_jog("JD"))
        self.btn_up.bind('<ButtonRelease-1>', lambda e: self.send_stop())

        self.btn_zero_force = tk.Button(jog_frame, text="⚖️ 하중 0점", command=self.zero_force, bg="#eee", height=2)
        self.btn_zero_force.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2,0))

        tk.Label(control_frame, text="3. 측정 모드 선택", font=("Arial", 12, "bold"), fg="#8e44ad").pack(anchor="w", pady=(0,5))
        self.mode_var = tk.StringVar(value="COMPRESSION")
        
        rb1 = tk.Radiobutton(control_frame, text="압축 강도 측정 (X축: 거리)", variable=self.mode_var, value="COMPRESSION", command=self.on_mode_change)
        rb1.pack(anchor="w")
        rb2 = tk.Radiobutton(control_frame, text="Poroelasticity 측정 (X축: 시간)", variable=self.mode_var, value="PORO", command=self.on_mode_change)
        rb2.pack(anchor="w", pady=(0,10))

        tk.Label(control_frame, text="4. 자동 압축 시작", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,5))
        
        # ⭐ 저장 경로 선택 UI 추가
        dir_frame = tk.Frame(control_frame)
        dir_frame.pack(fill=tk.X, pady=(0,5))
        tk.Label(dir_frame, text="저장 경로:").pack(side=tk.LEFT)
        self.ent_savedir = tk.Entry(dir_frame)
        self.ent_savedir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5,5))
        self.btn_dir = tk.Button(dir_frame, text="폴더 선택", command=self.select_directory)
        self.btn_dir.pack(side=tk.RIGHT)

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
        self.ent_dist.bind('<KeyRelease>', self.update_graph_limits)
        
        tk.Label(dist_speed_frame, text="속도(mm/s):").grid(row=0, column=2, sticky="w")
        self.ent_speed = tk.Entry(dist_speed_frame, width=8)
        self.ent_speed.insert(0, "0.5")
        self.ent_speed.grid(row=0, column=3, padx=(2,0))

        self.btn_start = tk.Button(control_frame, text="▶ START (그래프 기록 시작)", command=self.start_test, bg="#dff0d8", font=("Arial", 10, "bold"), height=1)
        self.btn_start.pack(fill=tk.X, pady=(0,5))
        
        self.btn_stop = tk.Button(control_frame, text="■ 모터 정지 / 기록 종료 (STOP)", command=self.stop_test, bg="#f2dede", font=("Arial", 10, "bold"), height=1)
        self.btn_stop.pack(fill=tk.X, pady=(0,15))

        tk.Label(control_frame, text="실시간 하중 (N):", font=("Arial", 10, "bold")).pack(anchor="w")
        self.lbl_force = tk.Label(control_frame, text="0.0000 N", font=("Arial", 16, "bold"), fg="blue")
        self.lbl_force.pack(anchor="w")

        graph_frame = tk.Frame(self.root, padx=10, pady=10)
        graph_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Compressive Strength (Dist vs Force)")
        self.ax.set_xlabel("Distance (mm)")
        self.ax.set_ylabel("Force (N)")
        self.ax.grid(True)
        
        self.ax.set_xlim(0, 5.0) 
        self.ax.set_ylim(-0.01, 0.5)
        self.ax.margins(x=0) 
        
        self.line, = self.ax.plot([], [], 'b-', linewidth=2)
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        self.update_graph_limits()

        entries = [self.ent_arduino, self.ent_indicator, self.ent_jog_speed, self.ent_savedir, self.ent_filename, self.ent_dist, self.ent_speed]
        for ent in entries:
            ent.bind('<Return>', lambda e: self.root.focus_set())

    # ⭐ 폴더 선택 다이얼로그 호출 함수
    def select_directory(self):
        selected_dir = filedialog.askdirectory(title="데이터를 저장할 폴더를 선택하세요")
        if selected_dir:
            self.ent_savedir.delete(0, tk.END)
            self.ent_savedir.insert(0, selected_dir)

    def clear_focus(self, event):
        if not isinstance(event.widget, tk.Entry):
            self.root.focus_set()

    def zero_force(self):
        raw_force_N = (self.current_weight_g / 1000.0) * 9.80665
        self.force_offset_N = raw_force_N

    def update_graph_limits(self, event=None):
        if self.is_running: return 
            
        mode = self.mode_var.get()
        if mode == "COMPRESSION":
            try:
                dist = float(self.ent_dist.get())
                x_limit = dist * 1.05 if dist > 0 else 5.0
                self.ax.set_xlim(0, x_limit)
                self.ax.set_xlabel("Distance (mm)")
            except ValueError:
                pass 
        else:
            self.ax.set_xlim(0, 60.0) 
            self.ax.set_xlabel("Time (s)")
            
        self.canvas.draw()

    def on_key_press(self, direction, event):
        if isinstance(event.widget, tk.Entry): return
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
            self.key_release_timer = None
        if self.current_jog_dir != direction:
            self.current_jog_dir = direction
            self.send_jog(direction)

    def on_key_release(self, event):
        if isinstance(event.widget, tk.Entry): return
        if self.key_release_timer:
            self.root.after_cancel(self.key_release_timer)
        self.key_release_timer = self.root.after(50, self.execute_key_release)

    def execute_key_release(self):
        self.current_jog_dir = None
        self.send_stop()

    def on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "COMPRESSION":
            self.ax.set_title("Compressive Strength (Dist vs Force)")
            self.ax.set_xlabel("Distance (mm)")
        else:
            self.ax.set_title("Poroelasticity / Stress Relaxation (Time vs Force)")
            self.ax.set_xlabel("Time (s)")
            
        self.ax.set_ylim(-0.01, 0.5)
        self.time_data.clear()
        self.dist_data.clear()
        self.force_data.clear()
        self.line.set_data([], [])
        self.update_graph_limits()

    def connect_devices(self):
        port_arduino = self.ent_arduino.get().strip().upper()
        port_indicator = self.ent_indicator.get().strip().upper()
        
        try:
            self.arduino = serial.Serial(port_arduino, 115200, timeout=0.1)
            self.indicator = serial.Serial(port_indicator, 115200, timeout=0.1)
            threading.Thread(target=self.read_indicator_thread, daemon=True).start()
            self.btn_connect.config(text="연결 성공!", bg="#dff0d8", state="disabled")
            messagebox.showinfo("연결 완료", "장비 연결 성공!")
        except Exception as e:
            messagebox.showerror("연결 오류", f"포트 접속 실패!\n{e}")

    def read_indicator_thread(self):
        while self.indicator and self.indicator.is_open:
            try:
                line = self.indicator.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    match = re.search(r'([-+]\s*\d+\.?\d*)', line)
                    if match:
                        val_str = match.group(1).replace(' ', '')
                        val = float(val_str)
                        
                        if abs(val) > 50000: continue

                        self.current_weight_g = -val
                        
                        raw_force_N = (self.current_weight_g / 1000.0) * 9.80665
                        net_force_N = raw_force_N - self.force_offset_N
                        
                        self.lbl_force.config(text=f"{net_force_N:.4f} N")

                        if self.is_running:
                            elapsed = time.time() - self.start_time

                            if not self.motor_reached_target:
                                motor_elapsed = time.time() - self.start_motor_time
                                dist_now = motor_elapsed * self.current_speed
                                
                                if dist_now >= self.target_dist:
                                    dist_now = self.target_dist
                                    self.motor_reached_target = True
                                    self.send_stop()
                                    if self.mode_var.get() == "COMPRESSION":
                                        self.is_running = False
                            else:
                                dist_now = self.target_dist

                            self.time_data.append(elapsed)
                            self.dist_data.append(dist_now)
                            self.force_data.append(net_force_N)

                            if self.csv_writer:
                                self.csv_writer.writerow([round(elapsed, 3), round(dist_now, 4), round(net_force_N, 4)])
                                
            except Exception: pass

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
        if self.csv_file and not self.csv_file.closed:
            try: self.csv_file.close()
            except: pass

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
        
        self.update_graph_limits()

        if self.csv_file and not self.csv_file.closed:
            try: self.csv_file.close()
            except: pass

        user_filename = self.ent_filename.get().strip()
        save_dir = self.ent_savedir.get().strip()
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if not user_filename:
            user_filename = "Compress" if self.mode_var.get() == "COMPRESSION" else "Poroelasticity"
            
        base_filename = f"{user_filename}_{now_str}.csv"
        
        # ⭐ 저장 경로가 지정되어 있으면 해당 폴더에, 아니면 기본 폴더에 저장
        if save_dir:
            self.csv_filename = os.path.join(save_dir, base_filename)
        else:
            self.csv_filename = base_filename
            
        self.csv_file = open(self.csv_filename, 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(["Time (s)", "Distance (mm)", "Force (N)"])

        cmd = f"start,{self.target_dist},{self.current_speed}\n"
        self.arduino.write(cmd.encode('utf-8'))
        
        self.motor_reached_target = False
        self.start_time = time.time()
        self.start_motor_time = time.time()
        self.is_running = True
        
        self.update_loop()

    def update_loop(self):
        if not self.is_running: return

        mode = self.mode_var.get()
        l = min(len(self.time_data), len(self.dist_data), len(self.force_data))
        
        if l > 0:
            t_data = self.time_data[:l]
            d_data = self.dist_data[:l]
            f_data = self.force_data[:l]

            if mode == "COMPRESSION":
                self.line.set_data(d_data, f_data)
                if d_data[-1] > self.ax.get_xlim()[1]:
                    self.ax.set_xlim(0, d_data[-1] * 1.05)
            else:
                self.line.set_data(t_data, f_data)
                if t_data[-1] > self.ax.get_xlim()[1]:
                    self.ax.set_xlim(0, t_data[-1] * 1.2)
            
            max_f = max(f_data)
            min_f = min(f_data)
            
            current_ymin, current_ymax = self.ax.get_ylim()
            if max_f > current_ymax or min_f < current_ymin:
                self.ax.set_ylim(min_f - 0.05, max_f + 0.1)

            self.canvas.draw()

        if self.is_running:
            self.root.after(50, self.update_loop)

if __name__ == "__main__":
    root = tk.Tk()
    app = HydrogelTesterApp(root)
    root.mainloop()