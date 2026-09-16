def _positive_number(value: str | float, label: str) -> float:
    number = float(value)
    if number <= 0:
        raise ValueError(f"{label}은(는) 0보다 커야 합니다.")
    return number


def jog_command(direction: str, speed: str | float) -> bytes:
    if direction not in {"JU", "JD"}:
        raise ValueError("지원하지 않는 조그 방향입니다.")
    _positive_number(speed, "속도")
    return f"{direction},{str(speed).strip()}\n".encode("utf-8")


def start_command(distance: str | float, speed: str | float) -> bytes:
    _positive_number(distance, "거리")
    _positive_number(speed, "속도")
    return f"start,{str(distance).strip()},{str(speed).strip()}\n".encode("utf-8")


def stop_command() -> bytes:
    return b"STOP\n"
