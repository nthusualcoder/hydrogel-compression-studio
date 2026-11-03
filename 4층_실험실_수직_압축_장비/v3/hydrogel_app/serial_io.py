"""직렬 포트 래퍼와 데모용 가상 장비."""
import math
import threading
import time
from collections import deque


def open_serial(port, baudrate, timeout=0.1):
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError(
            "pyserial이 설치되어 있지 않습니다. "
            "'python -m pip install -r requirements.txt'를 실행하세요."
        ) from exc
    return serial.Serial(port, baudrate, timeout=timeout)


class MemorySerial:
    """테스트용 메모리 시리얼. write 내용을 기록하고 queue_line으로 수신을 흉내낸다."""

    def __init__(self, port="DEMO", baudrate=115200, timeout=0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self.writes = []
        self._lines = deque()
        self._lock = threading.Lock()

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def readline(self):
        with self._lock:
            if self._lines:
                return self._lines.popleft()
        time.sleep(self.timeout)
        return b""

    def queue_line(self, line):
        with self._lock:
            self._lines.append(line if isinstance(line, bytes) else line.encode("utf-8"))

    def close(self):
        self.is_open = False


class DemoRig:
    """하드웨어 없이 UI를 확인하기 위한 가상 모터·로드셀.

    - 모터 포트: start/조그/STOP/MOFF/MON 명령을 받아 DONE 등 응답을 돌려준다.
    - 로드셀 포트: 10 Hz로 'xxx.x' 형식(g)의 값을 보낸다. 누르는 동안 양수로 증가한다.
    """

    def __init__(self, press_cmd, release_cmd):
        self.motor = MemorySerial("DEMO_MOTOR")
        self.indicator = MemorySerial("DEMO_LOADCELL")
        self.press_cmd = press_cmd
        self.release_cmd = release_cmd
        self.position_mm = 0.0
        self.velocity = 0.0          # mm/s, 양수 = 누르는 방향
        self.remaining = None        # 자동 이동 잔여 거리
        self.enabled = True
        self._stop = False
        self._diag = False
        self._diag_boot = time.monotonic()
        self._diag_last_status = self._diag_boot
        self._diag_seq = self._diag_skipped = 0
        self._diag_position = 0
        self._orig_write = self.motor.write
        self.motor.write = self._on_write
        threading.Thread(target=self._run, daemon=True).start()

    def _on_write(self, data):
        self._orig_write(data)
        cmd = data.decode("ascii", errors="ignore").strip()
        if cmd == "DIAG,ON":
            self._diag = True
            self._diag_seq = self._diag_skipped = 0
            self._diag_position = round(self.position_mm * 800)
            self._diag_send("ON", "1")
            return len(data)
        elif cmd == "DIAG,OFF":
            if self._diag:
                self._diag_send("OFF", f"0:{self._diag_skipped}")
            self._diag = False
            return len(data)
        if self._diag:
            self._diag_send("C", cmd)
        if cmd.startswith("start,"):
            _, dist, speed = cmd.split(",")
            self.remaining = float(dist)
            self.velocity = float(speed)
            self.enabled = True
        elif cmd.startswith(self.press_cmd + ","):
            self.velocity = float(cmd.split(",")[1])
            self.remaining = None
            self.enabled = True
        elif cmd.startswith(self.release_cmd + ","):
            self.velocity = -float(cmd.split(",")[1])
            self.remaining = None
            self.enabled = True
        elif cmd == "STOP":
            self.velocity = 0.0
            self.remaining = None
            self.motor.queue_line("STOPPED\n")
        elif cmd == "MOFF":
            self.velocity = 0.0
            self.remaining = None
            self.enabled = False
            self.motor.queue_line("MOTOR_OFF\n")
        elif cmd == "MON":
            self.enabled = True
            self.motor.queue_line("MOTOR_ON\n")
        return len(data)

    def _run(self):
        dt = 0.1
        while not self._stop and self.motor.is_open:
            step_velocity = self.velocity
            if self.velocity and self.enabled:
                step = self.velocity * dt
                if self.remaining is not None:
                    step = min(step, self.remaining)
                    self.remaining -= step
                self.position_mm += step
                if self.remaining is not None and self.remaining <= 1e-9:
                    self.remaining = None
                    self.velocity = 0.0
                    self.motor.queue_line("DONE\n")
            if self._diag:
                position = round(self.position_mm * 800)
                delta = position - self._diag_position
                if delta:
                    direction = 1 if delta > 0 else -1
                    if abs(step_velocity) <= 0.010001 and step_velocity:
                        for _ in range(abs(delta)):
                            self._diag_seq += 1
                            self._diag_position += direction
                            self._diag_send("S", str(round(1_000_000 / abs(step_velocity * 800))))
                    else:
                        self._diag_seq += abs(delta)
                        self._diag_skipped += abs(delta)
                        self._diag_position = position
                if time.monotonic() - self._diag_last_status >= 1:
                    self._diag_last_status = time.monotonic()
                    self._diag_send("H", f"0:{self._diag_skipped}")
            # 가상 하이드로젤(D10 H5, E≈20 kPa 가정): 0.2 mm 이후 접촉, 이후 거리에 따라 양수 하중 증가
            contact = max(0.0, self.position_mm - 0.2)
            grams = (contact * 32.0 + contact ** 3 * 4.0) + math.sin(time.time() * 7) * 0.05
            self.indicator.queue_line(f"{grams:8.1f}\r\n")
            time.sleep(dt)

    def _diag_send(self, kind, detail):
        # 데모 시각은 0.1초 가상 갱신 주기에 기반한다. 실물 타이밍 분석에 쓰지 않는다.
        tick = int((time.monotonic() - self._diag_boot) * 1_000_000) & 0xFFFFFFFF
        self.motor.queue_line(f"DBG,{kind},{tick},{self._diag_seq},{self._diag_position},{detail}\n")

    def close(self):
        self._stop = True
        self.motor.close()
        self.indicator.close()
