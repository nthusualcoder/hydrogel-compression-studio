"""1층 바이오룸 수평 압축 장비 설정. 하드웨어 관련 값은 모두 여기에서만 정의한다."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    title: str
    location: str
    board: str
    loadcell: str
    press_cmd: str           # 시편을 누르는 방향의 조그 명령
    release_cmd: str         # 시편에서 멀어지는 방향의 조그 명령
    press_label: str
    release_label: str
    press_key: str           # Tk keysym
    release_key: str
    default_motor_port: str
    default_indicator_port: str
    max_load_g: float        # 로드셀 정격 용량 (경고용)


PROFILE = DeviceProfile(
    key="1F",
    title="Hydrogel Compression Studio · 1F",
    location="1층 바이오룸 · 수평 압축 장비",
    board="Arduino Mega 2560 + RAMPS 1.4",
    loadcell="BCL-3L (3 kg)",
    press_cmd="JF",
    release_cmd="JB",
    press_label="◀  전진 (누르기)",
    release_label="▶  후진",
    press_key="Left",
    release_key="Right",
    default_motor_port="COM3",
    default_indicator_port="COM4",
    max_load_g=3000.0,
)
