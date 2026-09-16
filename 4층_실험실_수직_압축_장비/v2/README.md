# Hydrogel Compression Studio v2

4층 실험실용 수직 압축 장비의 새 PC 제어 UI이다. v1의 Arduino 직렬 명령 규약을 유지한다.

## VS Code 실행

1. VS Code에서 `4층_실험실_수직_압축_장비/v2` 폴더를 연다.
2. `Run and Debug`에서 `v2 UI (Demo)`를 선택하면 장비 없이 UI를 확인할 수 있다.
3. 실제 장비 사용 시 `v2 UI`를 선택한다.

터미널에서 특정 테마로 바로 확인할 수도 있다.

```powershell
python main.py --demo --theme light
python main.py --demo --theme dark
```

필요 패키지 설치:

```powershell
python -m pip install -r requirements.txt
```

테스트:

```powershell
python -m unittest discover -s tests -v
```

## 설정 저장

UI의 `기본값 저장` 버튼을 누르면 다음 위치에 JSON 설정이 저장된다.

```text
%APPDATA%\HydrogelTesterV2\settings.json
```

저장 항목은 모터 포트, 로드셀 포트, 데이터 저장 폴더와 화면 테마이다.

모터와 로드셀 통신 속도는 기존 장비 규약에 맞춰 모두 `115200 bps`로 고정되어 있다.

## 현재 직렬 명령

- 하강 누름: `JU,<speed>\n`
- 상승 누름: `JD,<speed>\n`
- 조그 버튼 놓음 또는 정지: `STOP\n`
- 측정 시작: `start,<distance>,<speed>\n` (v1의 숫자 표기 형식 유지)

## EXE 빌드

최종 배포 단계에서 다음 명령을 사용한다.

```powershell
pyinstaller --noconfirm --clean hydrogel_v2.spec
```
