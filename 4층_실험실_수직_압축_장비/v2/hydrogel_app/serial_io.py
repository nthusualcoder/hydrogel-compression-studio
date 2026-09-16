from collections import deque


class MemorySerial:
    """In-memory serial port used by demo mode and UI tests."""

    def __init__(self, port="DEMO", baudrate=115200, timeout=0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self.writes = []
        self._lines = deque()

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def readline(self):
        return self._lines.popleft() if self._lines else b""

    def queue_line(self, line):
        self._lines.append(line if isinstance(line, bytes) else line.encode("utf-8"))

    def close(self):
        self.is_open = False


def open_serial(port, baudrate, timeout=0.1):
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError(
            "pyserial이 설치되어 있지 않습니다. "
            "'python -m pip install -r requirements.txt'를 실행하세요."
        ) from exc
    return serial.Serial(port, baudrate, timeout=timeout)

