"""펌웨어(AccelStepper)의 사다리꼴 속도 프로파일을 그대로 재현해 시각 t의 명령 위치를 계산한다.

펌웨어는 정지 상태에서 가속도 a로 가속해 최고 속도 v에 도달한 뒤 등속으로 이동하고,
목표 지점 앞에서 같은 가속도로 감속해 멈춘다. 이동 거리가 짧아 v에 도달하지 못하면
삼각형 프로파일이 된다. 이전 코드는 위치 = t × v 로 계산해 가속 구간만큼 위치를
과대평가했다(1 mm/s 에서 약 0.2 mm). 이 오차는 변형률 축에 그대로 들어간다.
"""
import math


def move_duration(distance: float, speed: float, accel: float) -> float:
    """총 이동 시간(초)."""
    if distance <= 0 or speed <= 0:
        return 0.0
    if accel <= 0:
        return distance / speed
    if distance < speed * speed / accel:          # 삼각형: 최고 속도에 못 미침
        return 2.0 * math.sqrt(distance / accel)
    return distance / speed + speed / accel        # 사다리꼴: 가속 + 등속 + 감속


def position_at(t: float, distance: float, speed: float, accel: float) -> float:
    """시각 t(초)에서의 명령 위치(mm). 0 ≤ 결과 ≤ distance."""
    if t <= 0 or distance <= 0 or speed <= 0:
        return 0.0
    if accel <= 0:
        return min(t * speed, distance)
    if distance < speed * speed / accel:
        peak = math.sqrt(accel * distance)
        t_acc = peak / accel
        total = 2.0 * t_acc
        if t < t_acc:
            return 0.5 * accel * t * t
        if t < total:
            return distance - 0.5 * accel * (total - t) ** 2
        return distance
    t_acc = speed / accel
    d_acc = 0.5 * accel * t_acc * t_acc
    t_cruise = (distance - 2.0 * d_acc) / speed
    total = 2.0 * t_acc + t_cruise
    if t < t_acc:
        return 0.5 * accel * t * t
    if t < t_acc + t_cruise:
        return d_acc + speed * (t - t_acc)
    if t < total:
        return distance - 0.5 * accel * (total - t) ** 2
    return distance
