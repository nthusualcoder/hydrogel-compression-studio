"""PC → Arduino 직렬 명령. 두 장비 펌웨어가 공통으로 이해하는 규약이다.

  start,<mm>,<mm/s>   자동 이동 (양수 = 누르는 방향). 완료 시 Arduino가 DONE 송신
  <JOG>,<mm/s>        조그 (JOG = 장비별 press/release 명령)
  STOP                감속 정지. 드라이버는 여자 상태 유지 → STOPPED
  MOFF                즉시 정지 + 드라이버 전원 차단 → MOTOR_OFF
  MON                 드라이버 전원 인가 → MOTOR_ON
"""
import re

_NUMBER = re.compile(r"([-+])?\s*(\d+(?:\.\d+)?)")


def _positive_number(value, label: str) -> float:
    try:
        number = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{label}에 숫자를 입력하세요.") from exc
    if number <= 0:
        raise ValueError(f"{label}은(는) 0보다 커야 합니다.")
    return number


def jog_command(direction_cmd: str, speed) -> bytes:
    value = _positive_number(speed, "이동 속도")
    return f"{direction_cmd},{value:g}\n".encode("ascii")


def start_command(distance, speed) -> bytes:
    d = _positive_number(distance, "목표 거리")
    s = _positive_number(speed, "이동 속도")
    return f"start,{d:g},{s:g}\n".encode("ascii")


def stop_command() -> bytes:
    return b"STOP\n"


def motor_off_command() -> bytes:
    return b"MOFF\n"


def motor_on_command() -> bytes:
    return b"MON\n"


def parse_indicator_grams(line: str) -> float | None:
    """CI-1580A 출력 한 줄에서 g 단위 값을 읽는다.

    인디케이터는 'xxx.x' 형식(0.1 = 0.1 g)으로 보내며, 압축 시 음수가 된다.
    단위 문자가 있어도 무시하고 첫 번째 숫자를 사용한다.
    """
    match = _NUMBER.search(line)
    if not match:
        return None
    value = float(match.group(2))
    if match.group(1) == "-":
        value = -value
    if abs(value) > 50000:
        return None
    return value


def grams_to_newton(grams: float) -> float:
    return grams / 1000.0 * 9.80665
