import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


APP_NAME = "HydrogelStudio"


@dataclass
class AppSettings:
    motor_port: str = ""
    indicator_port: str = ""
    save_directory: str = ""
    theme: str = "dark"
    jog_speed: str = "0.5"
    distance: str = "2.0"     # 기본 시편 높이(5 mm)보다 작아야 한다
    speed: str = "0.5"
    sample_rate: str = "10"   # 초당 기록 수. "0" = 인디케이터 원본 전부
    diameter: str = "10"     # 시편 직경 mm (원통형)
    height: str = "5"        # 시편 초기 높이 mm


def default_config_path(device_key: str) -> Path:
    base = Path(os.environ.get("APPDATA", Path.home()))
    return base / APP_NAME / f"settings_{device_key}.json"


def load_settings(path: Path, defaults: AppSettings | None = None) -> AppSettings:
    settings = defaults or AppSettings()
    if not path.exists():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        merged = asdict(settings)
        merged.update({k: v for k, v in data.items() if k in merged and isinstance(v, str)})
        return AppSettings(**merged)
    except (OSError, ValueError, TypeError):
        return settings


def save_settings(settings: AppSettings, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
