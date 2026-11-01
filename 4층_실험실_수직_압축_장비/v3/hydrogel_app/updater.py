"""GitHub Releases에서 최신 exe를 확인·다운로드·교체한다.

버전 규칙 vX.Y.Z: X 대규모 기능, Y 소규모 기능 추가, Z 디자인·소규모 수정.
공개 저장소면 토큰 없이 동작한다.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

GITHUB_REPO = "nthusualcoder/hydrogel-compression-studio"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
_UA = {"User-Agent": "HydrogelStudio-updater", "Accept": "application/vnd.github+json"}


def parse_version(text: str):
    """'v3.1.2' / '3.1.2' → (3, 1, 2). 형식이 다르면 (0,0,0)."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def fetch_latest(timeout=10):
    """최신 릴리스 정보 {'tag','name','assets':[{name,url,size}],'notes','html_url'}를 돌려준다."""
    req = urllib.request.Request(API_LATEST, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {
        "tag": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "notes": data.get("body", "") or "",
        "html_url": data.get("html_url", RELEASES_PAGE),
        "assets": [
            {"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
            for a in data.get("assets", [])
        ],
    }


def find_asset(release, asset_name: str):
    for a in release["assets"]:
        if a["name"].lower() == asset_name.lower():
            return a
    return None


def download(url: str, dest: Path, progress_cb=None, timeout=30) -> Path:
    """dest에 내려받는다. progress_cb(received_bytes, total_bytes) 호출."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": _UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if progress_cb:
                progress_cb(got, total)
    # 연결이 중간에 끊겨도 읽기는 오류 없이 끝날 수 있다. 잘린 파일로 exe를 교체하면 안 된다
    if total and got != total:
        raise IOError(f"다운로드가 중간에 끊겼습니다 ({got}/{total} bytes)")
    if dest.exists():
        dest.unlink()
    tmp.rename(dest)
    return dest


def running_exe() -> Path | None:
    """PyInstaller exe로 실행 중이면 그 경로, 아니면 None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def replace_and_restart(new_file: Path, current_exe: Path):
    """실행 중인 exe를 종료한 뒤 새 파일로 바꾸고 다시 실행하는 배치 스크립트를 띄운다."""
    # onefile exe는 프로세스가 둘이다. 이 코드는 자식에서 돌고, 부트로더 부모는 자식이 끝난 뒤 _MEI 폴더를 지운 다음에야 끝나며
    # 그때까지 exe를 잡고 있어 move가 실패한다. 그래서 부모를 기다린다 (onefile 빌드 전제).
    pid = os.getppid()
    bat = Path(tempfile.gettempdir()) / "hydrogel_update.bat"
    # cmd.exe는 배치 파일을 시스템 ANSI 코드페이지(한국어 Windows: CP949)로 읽으므로
    # 한글 경로가 깨지지 않도록 mbcs 로 저장한다. PID 검사는 앞뒤 공백을 포함해
    # 정확히 그 숫자만 맞춘다 (1234 가 12345 에 걸리지 않게).
    bat.write_text(
        "@echo off\r\n"
        ":wait\r\n"
        f"tasklist /FI \"PID eq {pid}\" | find \" {pid} \" >nul && (timeout /t 1 /nobreak >nul & goto wait)\r\n"
        f"copy /Y \"{current_exe}\" \"{current_exe}.bak\" >nul\r\n"
        f"move /Y \"{new_file}\" \"{current_exe}\" >nul\r\n"
        f"start \"\" \"{current_exe}\"\r\n"
        "del \"%~f0\"\r\n",
        encoding="mbcs",
    )
    # 새 exe가 이 프로세스의 환경을 물려받으면 자신을 이 프로그램의 자식으로 여겨, 이미 지워진 _MEI 폴더에서 DLL을 찾다가 실패한다
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
