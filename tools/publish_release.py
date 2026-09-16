"""GitHub Release를 만들고 두 층의 exe를 첨부한다.

사용법 (저장소 루트에서):
    set GITHUB_TOKEN=ghp_xxx          (repo 권한이 있는 Personal Access Token)
    python tools\\publish_release.py v3.0.0 "릴리스 제목" [--notes 파일.md]

- 태그 v3.0.0 이 이미 푸시되어 있어야 한다 (git push --tags).
- 첨부 파일: 1층_바이오룸_수평_압축_장비/v3/HydrogelStudio_1F.exe, 4층_실험실_수직_압축_장비/v3/HydrogelStudio_4F.exe
- 같은 태그의 릴리스가 이미 있으면 그 릴리스에 파일만 다시 올린다(기존 동명 파일은 교체).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "nthusualcoder/hydrogel-compression-studio"
ROOT = Path(__file__).resolve().parent.parent
ASSETS = [
    ROOT / "1층_바이오룸_수평_압축_장비" / "v3" / "HydrogelStudio_1F.exe",
    ROOT / "4층_실험실_수직_압축_장비" / "v3" / "HydrogelStudio_4F.exe",
]


def api(method, url, token, data=None, content_type="application/json"):
    body = None
    if data is not None:
        body = data if isinstance(data, (bytes, bytearray)) else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "hydrogel-release-script",
        "Content-Type": content_type,
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise SystemExit(f"GitHub API {method} {url} -> {exc.code}\n{detail}") from exc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", help="예: v3.0.0")
    ap.add_argument("title", nargs="?", default=None)
    ap.add_argument("--notes", help="릴리스 노트 마크다운 파일")
    ap.add_argument("--prerelease", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("환경 변수 GITHUB_TOKEN 이 필요합니다. https://github.com/settings/tokens 에서 repo 권한 토큰을 만드세요.")
    for a in ASSETS:
        if not a.exists():
            raise SystemExit(f"첨부 파일이 없습니다: {a}")

    notes = Path(args.notes).read_text(encoding="utf-8") if args.notes else ""
    base = f"https://api.github.com/repos/{REPO}"

    # 기존 릴리스 확인
    try:
        rel = api("GET", f"{base}/releases/tags/{args.tag}", token)
        print(f"기존 릴리스 사용: {rel['html_url']}")
    except SystemExit:
        rel = api("POST", f"{base}/releases", token, {
            "tag_name": args.tag,
            "name": args.title or args.tag,
            "body": notes,
            "draft": False,
            "prerelease": args.prerelease,
        })
        print(f"릴리스 생성: {rel['html_url']}")

    existing = {a["name"]: a["id"] for a in rel.get("assets", [])}
    upload_url = rel["upload_url"].split("{")[0]
    for path in ASSETS:
        if path.name in existing:
            api("DELETE", f"{base}/releases/assets/{existing[path.name]}", token)
            print(f"기존 첨부 삭제: {path.name}")
        print(f"업로드 중: {path.name} ({path.stat().st_size / 1e6:.0f} MB) …", end="", flush=True)
        api("POST", f"{upload_url}?name={path.name}", token, path.read_bytes(), content_type="application/octet-stream")
        print(" 완료")
    print(f"\n완료: {rel['html_url']}")


if __name__ == "__main__":
    sys.exit(main())
