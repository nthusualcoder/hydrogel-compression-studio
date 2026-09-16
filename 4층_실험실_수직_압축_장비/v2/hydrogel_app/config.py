import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


APP_NAME = "HydrogelTesterV2"


@dataclass
class AppSettings:
    motor_port: str = "COM10"
    indicator_port: str = "COM11"
    save_directory: str = ""
    theme: str = "dark"


def default_config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home()))
    return base / APP_NAME / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    config_path = path or default_config_path()
    if not config_path.exists():
        return AppSettings()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        defaults = asdict(AppSettings())
        defaults.update({key: value for key, value in data.items() if key in defaults})
        return AppSettings(**defaults)
    except (OSError, ValueError, TypeError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path
