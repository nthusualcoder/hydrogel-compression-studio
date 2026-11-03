"""선택형 STEP 진단 CSV. 수신 데이터를 메모리에 쌓지 않고 파일에 기록한다."""
import csv
import json
import math
import time
from datetime import datetime


FIELDS = (
    "pc_time", "pc_elapsed_s", "event", "mcu_time_us", "mcu_elapsed_us",
    "step_seq", "position_steps", "interval_us", "scheduled_interval_us", "seq_gap",
    "command", "expected_interval_us", "grams", "force_N", "write_duration_us", "detail",
)


class StepLog:
    def __init__(self, path, *, demo=False):
        self.path = path
        self.file = path.open("x", newline="", encoding="utf-8-sig")
        self.started = time.monotonic()
        self.last_rx = self.started
        self.last_flush = self.started
        self.active = False
        self.command = ""
        self.expected_interval = ""
        self.previous_step = None
        self.last_step_seq = 0
        self.last_mcu = None
        self.mcu_elapsed = 0
        self.step_count = 0
        metadata = {
            "format": "hydrogel_step_log_v1", "device": "4F", "demo": demo,
            "steps_per_mm": 800, "full_step_log_max_speed_mm_s": 0.01,
            "measurement": "software timestamp near STEP HIGH output; not voltage or shaft feedback",
            "clocks": "PC and MCU clocks are independent; pc_elapsed_s uses receive/send time; use MCU intervals for STEP timing",
        }
        self.file.write("# " + json.dumps(metadata, ensure_ascii=False) + "\n")
        self.writer = csv.DictWriter(self.file, fieldnames=FIELDS)
        self.writer.writeheader()
        self.record("SESSION_START")

    def record(self, event, received_at=None, **values):
        now = time.monotonic()
        at = now if received_at is None else received_at
        self.writer.writerow(dict(
            pc_time=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            pc_elapsed_s=f"{at - self.started:.6f}", event=event,
            command=self.command, expected_interval_us=self.expected_interval, **values,
        ))
        if now - self.last_flush >= 0.5 or event in ("SESSION_START", "SESSION_END", "ON", "OFF"):
            self.file.flush()
            self.last_flush = now

    def receive(self, line, received_at):
        if not line.startswith("DBG,"):
            self.record("RX", received_at, detail=line)
            return None
        parts = line.split(",", 5)
        if len(parts) != 6 or parts[1] not in ("ON", "OFF", "S", "C", "H"):
            raise ValueError("알 수 없는 STEP 진단 형식")
        _, kind, raw_time, raw_seq, raw_position, payload = parts
        tick, seq, position = int(raw_time), int(raw_seq), int(raw_position)
        if not 0 <= tick < 2**32 or not 0 <= seq < 2**32:
            raise ValueError("STEP 진단 카운터 범위 오류")
        if kind == "ON":
            if self.active:
                raise ValueError("기록 중 Arduino 진단 세션이 다시 시작되었습니다")
            if payload != "1":
                raise ValueError("지원하지 않는 STEP 진단 프로토콜")
            self.active = True
            self.last_mcu = tick
            self.mcu_elapsed = 0
            self.previous_step = None
            self.last_step_seq = seq
        elif not self.active:
            self.record("RX_BEFORE_ON", received_at, detail=line)
            return None
        else:
            delta = (tick - self.last_mcu) & 0xFFFFFFFF
            if delta > 2**31:
                raise ValueError("Arduino 시각 역전 또는 재시작 감지")
            self.mcu_elapsed += delta
            self.last_mcu = tick
        self.last_rx = received_at
        values = dict(mcu_time_us=tick, mcu_elapsed_us=self.mcu_elapsed,
                      step_seq=seq, position_steps=position, detail=payload)
        if kind == "C":
            self.command = payload
            command = payload.split(",")
            if command[0] in ("start", "JU", "JD"):
                speed = float(command[-1])
                if not math.isfinite(speed) or speed <= 0:
                    raise ValueError("진단 명령의 속도가 올바르지 않습니다")
                self.expected_interval = 1_000_000 / min(4000, max(1, speed * 800))
                self.previous_step = None  # 새 이동의 유휴 시간을 틱 간격으로 계산하지 않는다.
        elif kind == "S":
            scheduled = int(payload)
            if scheduled <= 0:
                raise ValueError("STEP 예약 간격 오류")
            gap = ((seq - self.last_step_seq) & 0xFFFFFFFF) - 1
            if gap < 0:
                raise ValueError("STEP 순번 중복")
            values.update(seq_gap=gap, scheduled_interval_us=scheduled)
            if self.previous_step is not None and gap == 0:
                values["interval_us"] = self.mcu_elapsed - self.previous_step
            self.previous_step = self.mcu_elapsed
            self.last_step_seq = seq
            self.step_count += 1
        elif kind in ("H", "OFF"):
            counts = payload.split(":")
            if len(counts) != 2 or any(not n.isdigit() for n in counts):
                raise ValueError("STEP 누락 카운터 형식 오류")
        self.record({"S": "STEP", "C": "CMD", "H": "HEARTBEAT"}.get(kind, kind), received_at, **values)
        return kind

    def close(self, reason="user"):
        if not self.file.closed:
            try:
                self.record("SESSION_END", detail=reason)
            finally:
                self.file.close()


def summarize(path):
    """다른 PC에서 STEP 간격과 저장 누락을 메모리에 쌓지 않고 집계한다."""
    intervals = 0
    total = 0.0
    minimum, maximum = math.inf, 0.0
    steps = gaps = 0
    dropped = skipped = 0
    confirmed = complete = False
    demo = False
    with open(path, encoding="utf-8-sig", newline="") as file:
        metadata = file.readline()
        if not metadata.startswith("# "):
            raise ValueError("STEP 진단 CSV가 아닙니다")
        demo = json.loads(metadata[2:]).get("demo", False)
        for row in csv.DictReader(file):
            event = row["event"]
            if event == "ON":
                confirmed = True
            elif event == "OFF":
                complete = True
            if event in ("OFF", "HEARTBEAT"):
                lost, omitted = row["detail"].split(":")
                dropped, skipped = max(dropped, int(lost)), max(skipped, int(omitted))
            if event == "STEP":
                steps += 1
                gaps += int(row["seq_gap"])
                if row["interval_us"]:
                    value = float(row["interval_us"])
                    minimum, maximum = min(minimum, value), max(maximum, value)
                    total += value
                    intervals += 1
    return dict(demo=demo, firmware_confirmed=confirmed, complete=complete, steps=steps,
                sequence_gaps=gaps, dropped=dropped, skipped_fast=skipped,
                mean_interval_us=total / intervals if intervals else None,
                min_interval_us=minimum if intervals else None,
                max_interval_us=maximum if intervals else None)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="STEP 진단 CSV 요약 (모터의 실제 변위 측정은 아님)")
    parser.add_argument("csv_path")
    args = parser.parse_args()
    print(json.dumps(summarize(args.csv_path), ensure_ascii=False, indent=2))
