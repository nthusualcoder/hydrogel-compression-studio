"""4층 실험실 수직 압축 장비 설정. 하드웨어 관련 값은 모두 여기에서만 정의한다."""
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
    max_speed_mm_s: float    # 펌웨어 MAX_SPEED_SPS ÷ STEPS_PER_MM. 이보다 빠른 명령은 펌웨어가 잘라 버려 거리 추정이 어긋난다
    accel_mm_s2: float       # 펌웨어 ACCEL_SPS2 ÷ STEPS_PER_MM. 거리 추정(사다리꼴 프로파일)에 사용


PROFILE = DeviceProfile(
    key="4F",
    title="Hydrogel Compression Studio · 4F",
    location="4층 실험실 · 수직 압축 장비",
    board="Arduino UNO R4 Minima",
    loadcell="BCL-2L (2 kg)",
    press_cmd="JU",
    release_cmd="JD",
    press_label="▼  하강 (누르기)",
    release_label="▲  상승",
    press_key="Down",
    release_key="Up",
    default_motor_port="COM10",
    default_indicator_port="COM11",
    max_load_g=2000.0,
    max_speed_mm_s=4000.0 / 800.0,   # 5.0 mm/s
    accel_mm_s2=2000.0 / 800.0,      # 2.5 mm/s²
)
