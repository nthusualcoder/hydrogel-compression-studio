# Hydrogel Compression Studio

하이드로젤 압축 장비 두 대의 제어 프로그램과 펌웨어.

| 장비 | 폴더 | 실행 파일 |
|---|---|---|
| 1층 바이오룸 · 수평 압축 (Mega 2560 + RAMPS 1.4, BCL-3L 3 kg) | `1층_바이오룸_수평_압축_장비/v3/` | `HydrogelStudio_1F.exe` |
| 4층 실험실 · 수직 압축 (UNO R4 Minima, BCL-2L 2 kg) | `4층_실험실_수직_압축_장비/v3/` | `HydrogelStudio_4F.exe` |

두 프로그램은 서로 독립된 코드이며 각 폴더의 README에 사용법·직렬 규약·빌드 방법이 있다. `v1`, `v2`는 이전 버전 보존용이다. 각 폴더의 `history.md`에 작업 이력을, `soul/필수_지침.md`에 안전·검증 원칙을 둔다.

## 실행 파일 받기

exe는 저장소에 넣지 않고 **GitHub Releases**에 첨부한다.

- 프로그램 안의 **⬇ 업데이트 확인** 버튼을 누르면 최신 릴리스를 조회해 새 버전이 있을 때 exe를 내려받고, 원하면 지금 실행 중인 파일을 교체한 뒤 다시 실행한다.
- 수동: https://github.com/nthusualcoder/hydrogel-compression-studio/releases 에서 해당 층의 exe를 내려받는다.

## 버전 규칙 vX.Y.Z

| 자리 | 올리는 경우 | 예 |
|---|---|---|
| X | 대규모 기능 업데이트, 규약·데이터 형식이 바뀜 | 3.0.0 → 4.0.0 |
| Y | 소소한 기능 추가 | 3.0.0 → 3.1.0 |
| Z | 디자인 변경, 문구·소규모 수정 | 3.1.0 → 3.1.1 |

버전은 각 층 `v3/hydrogel_app/__init__.py`의 `__version__`에 있고, git 태그 `vX.Y.Z`와 릴리스 태그가 이 값과 같아야 업데이트 확인이 올바르게 동작한다. 두 층은 같은 버전 번호를 함께 올린다.

## 새 버전 배포 절차

```powershell
# 1) 두 층 __version__ 을 올리고 history.md 에 기록
# 2) 각 층 v3 폴더에서 테스트와 빌드
python -m unittest discover -s tests -v
pyinstaller --noconfirm --clean build.spec      # dist\HydrogelStudio_xF.exe → v3\ 로 이동
# 3) 커밋·태그·푸시 (저장소 루트)
git add -A
git commit -m "v3.1.0: ..."
git tag v3.1.0
git push origin main --tags
# 4) 릴리스 생성 + exe 첨부 (repo 권한 토큰 필요)
$env:GITHUB_TOKEN = "ghp_..."
python tools\publish_release.py v3.1.0 "v3.1.0 - 기능 요약"
```

릴리스는 GitHub 웹에서 "Draft a new release → 태그 선택 → exe 두 개 첨부"로 만들어도 된다. 첨부 파일 이름은 반드시 `HydrogelStudio_1F.exe`, `HydrogelStudio_4F.exe` 여야 프로그램이 찾는다.
