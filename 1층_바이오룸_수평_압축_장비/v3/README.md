# Hydrogel Compression Studio v3 · 1층 바이오룸 수평 압축 장비

Arduino Mega 2560 + RAMPS 1.4 + A4988 + BCL-3L(3 kg) + CI-1580A 구성의 수평 압축 장비 제어 프로그램이다.

## 폴더 구성

```
v3/
├─ HydrogelStudio_1F.exe   배포용 실행 파일 (현장에서는 이것만 실행)
├─ main.py                 python main.py [--demo] [--theme dark|light]
├─ hydrogel_app/
│  ├─ profile.py           이 장비의 설정 (조그 명령 JF/JB, 키보드 ←/→, COM3/COM4, 3 kg)
│  ├─ app.py               Tkinter GUI (VS Code Dark+/Light+ 스타일)
│  ├─ protocol.py          직렬 명령 생성, 인디케이터 파싱, g→N 환산
│  ├─ serial_io.py         pyserial 래퍼, 데모용 가상 장비
│  ├─ config.py            %APPDATA%\HydrogelStudio\settings_1F.json
│  └─ theme.py             색상 팔레트
├─ firmware/compression_1F_mega_ramps/   Arduino 펌웨어
├─ tests/test_v3.py        자동 테스트
├─ build.spec              PyInstaller 빌드 스크립트
└─ requirements.txt
```

## 실행 / 빌드

```powershell
python -m pip install -r requirements.txt
python main.py --demo                     # 장비 없이 가상 장비로 화면 확인
python main.py                            # 실제 장비
python -m unittest discover -s tests -v   # 테스트
pyinstaller --noconfirm --clean build.spec   # dist\HydrogelStudio_1F.exe → v3\ 로 복사
```

## 직렬 규약 (115200 bps, `\n` 종료)

| 명령 | 동작 | Arduino 응답 |
|---|---|---|
| `start,<mm>,<mm/s>` | 자동 이동 (양수 = 전진 = 누르는 방향) | 완료 시 `DONE` |
| `JF,<mm/s>` / `JB,<mm/s>` | 조그 전진 / 후진 (누르고 있는 동안) | |
| `STOP` | 감속 정지. 드라이버 여자 유지(위치 고정) | `STOPPED` |
| `MOFF` | 즉시 정지 + 드라이버 전원 차단 | `MOTOR_OFF` |
| `MON` | 드라이버 전원 인가 | `MOTOR_ON` |
| D3 버튼 (Active LOW) | 하드웨어 비상정지 | `STOP_BUTTON` |

핀: STEP 60, DIR 61, ENABLE 56 (RAMPS Y축), 800 steps/mm, 최대 5000 steps/s, 가속도 2000 steps/s².

로드셀 인디케이터는 `xxx.x` 형식으로 g 값을 보내며 압축 시 음수다. 프로그램은 부호를 반전해 압축력을 양수 N으로 표시·저장한다.

## 시험 흐름

1. 포트를 입력하고 **장비 연결**.
2. 조그 버튼(또는 ← → 키)으로 압축판을 시편 접촉 직전까지 이동.
3. **현재 하중을 0점으로**.
4. 모드 선택 후 **시험 시작**.
   - 압축 강도: 목표 거리 도달(`DONE`) 시 자동 종료.
   - Poroelasticity: 목표 거리 도달 후에도 변위를 유지한 채 계속 기록. **정지 · 기록 종료**로 끝낸다.
5. CSV: `Time (s), Distance (mm), Force (N)`, 파일명 `<이름>_YYYYMMDD_HHMMSS.csv`.

## 안전 버튼

- **비상 정지 (Esc)**: 이동만 멈춘다. 드라이버는 여자 상태라 축이 현재 위치를 유지한다.
- **모터 전원 OFF**: 드라이버 전원을 끊는다. 축이 자유 상태가 된다.
- **모터 전원 ON**: 드라이버 전원을 다시 인가한다. 조그·시험 시작 시에도 자동으로 인가된다.

## v2 대비 변경점

- 인디케이터 파서가 단위 문자 없는 `xxx.x` 형식을 읽지 못하던 문제 수정. 압축력 부호 반전 적용.
- Poroelasticity 모드가 모터 정지와 함께 끝나던 문제 수정.
- 정지 시 모터 전원이 꺼지던 동작 분리: `STOP`은 위치 유지, `MOFF`만 전원 차단.
- Arduino `DONE` 신호로 이동 완료 판정. 신호를 놓치면 시간 기준으로 보조 판정.
- VS Code 스타일 UI, 최대 하중 표시, 로드셀 용량 90% 초과 경고, 설정 자동 저장.
- 기록 속도(samples/s) 선택: 지정 간격으로 로드셀 값을 평균해 기록. GitHub Releases에서 새 exe를 내려받아 교체하는 "업데이트 확인" 버튼.
