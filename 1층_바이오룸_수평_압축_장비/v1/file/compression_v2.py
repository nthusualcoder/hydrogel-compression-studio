import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import threading
import time
import csv
from datetime import datetime

print("▶ 1단계: 모듈 로딩 완료")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

class HydrogelTesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Hydrogel Tester (Mega2560 + RAMPS 1.4)")
        self.root.geometry("1050x760") 

        self.arduino = None
        self.indicator = None
        self.is_running = False
        self.motor_reached_target = False
        
        self.current_weight_kg = 0.0
        self.force_offset_N = 0.0  
        self.start_time = 0
        self.start_motor_time = 0
        self.current_dist = 0.0
        self.target_dist = 0.0
        self.current_speed = 0.0
        
        self.current_jog_dir = None
        self.key_release_timer = None
        self.time_data, self.dist_data, self.force_data = [], [], []
        self.last_draw_time = 0
        
        self.save_dir = os.getcwd() 

        self.setup_ui()

        self.root.bind('<Button-1>', self.clear_focus)
        self.root.bind('<KeyPress-Left>', lambda e: self.on_key_press("JF", e))   
        self.root.bind('<KeyRelease-Left>', self.on_key_release)
        self.root.bind('<KeyPress-Right>', lambda e: self.on_key_press("JB", e)) 
        self.root.bind('<KeyRelease-Right>', self.on_key_release)

    def setup_ui(self):
        control_frame = tk.Frame(self.root, width=340, padx=10, pady=10)
        control_frame.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(control_frame, text="1. 포트 설정", font=("Arial", 11, "bold")).pack(anchor="w")
        p_frame = tk.Frame(control_frame)
        p_frame.pack(fill=tk.X, pady=5)
        tk.Label(p_frame, text="Mega:").pack(side=tk.LEFT)
        self.ent_arduino = tk.Entry(p_frame, width=8); self.ent_arduino.insert(0, "COM3")
        self.ent_arduino.pack(side=tk.LEFT, padx=5)
        tk.Label(p_frame, text="Loadcell:").pack(side=tk.LEFT)
        self.ent_indicator = tk.Entry(p_frame, width=8); self.ent_indicator.insert(0, "COM4")
        self.ent_indicator.pack(side=tk.LEFT, padx=5)
        self.btn_conn = tk.Button(control_frame, text="장비 연결", command=self.connect_devices, bg="#d9edf7")
        self.btn_conn.pack(fill=tk.X, pady=(0, 15))

        tk.Label(control_frame, text="2. 수동 제어 및 영점 (키보드 ←, → 사용)", font=("Arial", 11, "bold")).pack(anchor="w")
        js_frame = tk.Frame(control_frame); js_frame.pack(fill=tk.X)
        tk.Label(js_frame, text="속도(mm/s):").pack(side=tk.LEFT)
        self.ent_jog_speed = tk.Entry(js_frame, width=8); self.ent_jog_speed.insert(0, "0.5")
        self.ent_jog_speed.pack(side=tk.RIGHT)
        
        jog_frame = tk.Frame(control_frame); jog_frame.pack(fill=tk.X, pady=10)
        
        self.btn_fw = tk.Button(jog_frame, text="◀ 전진", bg="#fcf8e3", height=2)
        self.btn_fw.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        self.btn_fw.bind('<ButtonPress-1>', lambda e: self.send_jog("JF"))
        self.btn_fw.bind('<ButtonRelease-1>', lambda e: self.send_stop())
        
        self.btn_bw = tk.Button(jog_frame, text="▶ 후진", bg="#fcf8e3", height=2)
        self.btn_bw.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        self.btn_bw.bind('<ButtonPress-1>', lambda e: self.send_jog("JB"))
        self.btn_bw.bind('<ButtonRelease-1>', lambda e: self.send_stop())
        
        tk.Button(jog_frame, text="⚖️ 영점", command=self.zero_force, bg="#eee", height=2).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)

        tk.Label(control_frame, text="3. 측정 모드", font=("Arial", 11, "bold")).pack(anchor="w", pady=(15,0))
        self.mode_var = tk.StringVar(value="COMPRESSION")
        tk.Radiobutton(control_frame, text="압축 강도 (X:거리)", variable=self.mode_var, value="COMPRESSION", command=self.update_graph_limits).pack(anchor="w")
        tk.Radiobutton(control_frame, text="Poroelasticity (X:시간)", variable=self.mode_var, value="PORO", command=self.update_graph_limits).pack(anchor="w")

        tk.Label(control_frame, text="4. 자동 측정", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10,0))
        
        dir_frame = tk.Frame(control_frame)
        dir_frame.pack(fill=tk.X, pady=2)
        tk.Button(dir_frame, text="📁 저장 위치", command=self.browse_folder).pack(side=tk.LEFT)
        display_path = self.save_dir if len(self.save_dir) < 30 else "..." + self.save_dir[-27:]
        self.lbl_dir = tk.Label(dir_frame, text=display_path, fg="gray", anchor="w")
        self.lbl_dir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        f_frame = tk.Frame(control_frame); f_frame.pack(fill=tk.X, pady=5)
        tk.Label(f_frame, text="파일명:").pack(side=tk.LEFT)
        self.ent_fn = tk.Entry(f_frame); self.ent_fn.insert(0, "Sample_01"); self.ent_fn.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        
        ds_frame = tk.Frame(control_frame); ds_frame.pack(fill=tk.X, pady=5)
        tk.Label(ds_frame, text="거리:").grid(row=0, column=0)
        self.ent_dist = tk.Entry(ds_frame, width=7); self.ent_dist.insert(0, "5.0"); self.ent_dist.grid(row=0, column=1)
        self.ent_dist.bind('<KeyRelease>', self.update_graph_limits)
        tk.Label(ds_frame, text=" 속도:").grid(row=0, column=2)
        self.ent_speed = tk.Entry(ds_frame, width=7); self.ent_speed.insert(0, "0.5"); self.ent_speed.grid(row=0, column=3)

        tk.Button(control_frame, text="▶ START", command=self.start_test, bg="#dff0d8", font=("Arial", 10, "bold")).pack(fill=tk.X, pady=2)
        tk.Button(control_frame, text="■ STOP", command=self.stop_test, bg="#f2dede", font=("Arial", 10, "bold")).pack(fill=tk.X, pady=(0, 15))

        self.lbl_force = tk.Label(control_frame, text="0.0000 N", font=("Arial", 18, "bold"), fg="blue")
        self.lbl_force.pack(pady=10)

        self.fig = Figure(figsize=(5, 4), dpi=100); self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Ready"); self.ax.grid(True)
        self.line, = self.ax.plot([], [], 'b-')
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.canvas.get_tk_widget().pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.update_graph_limits()

        entries = [self.ent_arduino, self.ent_indicator, self.ent_jog_speed, self.ent_fn, self.ent_dist, self.ent_speed]
        for ent in entries:
            ent.bind('<Return>', lambda e: self.root.focus_set())

    def browse_folder(self):
        folder_selected = filedialog.askdirectory(initialdir=self.save_dir, title="데이터 저장 폴더 선택")
        if folder_selected: 
            self.save_dir = folder_selected
            display_path = self.save_dir if len(self.save_dir) < 30 else "..." + self.save_dir[-27:]
            self.lbl_dir.config(text=display_path)

    def clear_focus(self, event):
        if not isinstance(event.widget, tk.Entry):
            self.root.focus_set()

    def zero_force(self): self.force_offset_N = self.current_weight_kg * 9.80665
    
    def update_graph_limits(self, e=None):
        if self.is_running: return
        mode = self.mode_var.get()
        if mode == "COMPRESSION":
            try: d = float(self.ent_dist.get()); self.ax.set_xlim(0, d*1.1); self.ax.set_xlabel("Distance (mm)")
            except: pass
        else: self.ax.set_xlim(0, 60); self.ax.set_xlabel("Time (s)")
        self.canvas.draw()

    def connect_devices(self):
        try:
            self.arduino = serial.Serial(self.ent_arduino.get(), 115200, timeout=0.1)
            self.indicator = serial.Serial(self.ent_indicator.get(), 115200, timeout=0.02)
            threading.Thread(target=self.read_thread, daemon=True).start()
            self.btn_conn.config(text="연결됨", state="disabled")
        except Exception as e: messagebox.showerror("Error", str(e))

    def read_thread(self):
        while self.indicator and self.indicator.is_open:
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
                    time.sleep(0.001)
            except: pass

    def on_key_press(self, d, e):
        if isinstance(e.widget, tk.Entry): return
        if self.key_release_timer: self.root.after_cancel(self.key_release_timer); self.key_release_timer = None
        if self.current_jog_dir != d: self.current_jog_dir = d; self.send_jog(d)

    def on_key_release(self, e):
        if isinstance(e.widget, tk.Entry): return
        if self.key_release_timer: self.root.after_cancel(self.key_release_timer)
        self.key_release_timer = self.root.after(50, self.exec_release)

    def exec_release(self): self.current_jog_dir = None; self.send_stop()

    def send_jog(self, d):
        if self.arduino:
            cmd = f"{d},{self.ent_jog_speed.get()}\n"
            self.arduino.write(cmd.encode())

    def send_stop(self):
        if self.arduino: 
            self.arduino.write(b"STOP\n")

    def stop_test(self): self.is_running = False; self.send_stop()

    def start_test(self):
        if not self.arduino: return
        self.target_dist = float(self.ent_dist.get())
        self.current_speed = float(self.ent_speed.get())
        self.time_data, self.dist_data, self.force_data = [], [], []
        self.update_graph_limits()
        
        fn = f"{self.ent_fn.get()}_{datetime.now().strftime('%H%M%S')}.csv"
        full_path = os.path.join(self.save_dir, fn)
        
        self.csv_file = open(full_path, 'w', newline='')
        self.writer = csv.writer(self.csv_file); self.writer.writerow(["Time", "Dist", "Force"])
        
        cmd = f"start,{self.target_dist},{self.current_speed}\n"
        self.arduino.write(cmd.encode())
        
        self.is_running, self.motor_reached_target = True, False
        self.start_time = self.start_motor_time = time.time()
        self.last_draw_time = time.time()
        self.update_loop()

    def update_loop(self):
        if not self.is_running: self.csv_file.close(); return
        now = time.time(); dt = now - self.start_time
        if not self.motor_reached_target:
            d_now = (now - self.start_motor_time) * self.current_speed
            if d_now >= self.target_dist:
                d_now = self.target_dist; self.motor_reached_target = True; self.send_stop()
                if self.mode_var.get() == "COMPRESSION": self.is_running = False
        else: d_now = self.target_dist
        
        f_net = (self.current_weight_kg * 9.80665) - self.force_offset_N
        
        self.time_data.append(dt); self.dist_data.append(d_now); self.force_data.append(f_net)
        self.writer.writerow([round(dt,3), round(d_now,4), round(f_net,4)])
        
        # 0.1초마다 화면 강제 업데이트
        if now - self.last_draw_time > 0.1:
            if self.mode_var.get() == "COMPRESSION": self.line.set_data(self.dist_data, self.force_data)
            else: self.line.set_data(self.time_data, self.force_data)
            
            # 그래프 Y축 자동 확장
            if f_net > self.ax.get_ylim()[1]: self.ax.set_ylim(-0.2, f_net + 1)
            
            # Poroelasticity 모드일 때 60초가 넘어가면 X축 자동 확장
            if self.mode_var.get() == "PORO" and dt > self.ax.get_xlim()[1]:
                self.ax.set_xlim(0, dt + 10)
                
            self.canvas.draw()  # ⭐ draw_idle() -> draw() 로 변경하여 강제 즉시 그리기 수행
            self.last_draw_time = now
        
        self.root.after(10, self.update_loop)

if __name__ == "__main__":
    root = tk.Tk(); app = HydrogelTesterApp(root); root.mainloop()