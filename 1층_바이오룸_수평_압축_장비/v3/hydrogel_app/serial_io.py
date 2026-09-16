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
    - 로드셀 포트: 10 Hz로 'xxx.x' 형식(g)의 값을 보낸다. 누르는 동안 음수로 증가한다.
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
        self._orig_write = self.motor.write
        self.motor.write = self._on_write
        threading.Thread(target=self._run, daemon=True).start()

    def _on_write(self, data):
        self._orig_write(data)
        cmd = data.decode("ascii", errors="ignore").strip()
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
            # 가상 하이드로젤: 2 mm 이후 접촉, 이후 거리에 따라 하중 증가 (음수 = 압축)
            contact = max(0.0, self.position_mm - 2.0)
            grams = -(contact ** 1.5) * 6.0 + math.sin(time.time() * 7) * 0.05
            self.indicator.queue_line(f"{grams:8.1f}\r\n")
            time.sleep(dt)

    def close(self):
        self._stop = True
        self.motor.close()
        self.indicator.close()
