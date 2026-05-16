import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    discord_token: str
    command_prefix: str
    default_poll_interval_sec: int
    data_dir: str
    private_monitor_enabled: bool
    private_monitor_category_id: Optional[int]
    fast_assistant_enabled: bool
    fast_assistant_sound_enabled: bool
    fast_assistant_sound_path: str
    fast_assistant_max_per_minute: int
    fast_assistant_max_parallel_tabs: int
    fast_assistant_min_interval_sec: float


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set")
    prefix = os.getenv("COMMAND_PREFIX", "!").strip() or "!"
    poll = int(os.getenv("DEFAULT_POLL_INTERVAL_SEC", "22"))
    data_dir = os.getenv("DATA_DIR", "data").strip() or "data"
    sound_path = (os.getenv("FAST_ASSISTANT_SOUND_PATH") or "").strip()
    max_pm = int(os.getenv("FAST_ASSISTANT_MAX_PER_MINUTE", "10"))
    max_par = int(os.getenv("FAST_ASSISTANT_MAX_PARALLEL_TABS", "3"))
    min_iv = float(os.getenv("FAST_ASSISTANT_MIN_INTERVAL_SEC", "1.2"))
    cat_raw = (os.getenv("PRIVATE_MONITOR_CATEGORY_ID") or "").strip()
    private_cat: Optional[int] = None
    if cat_raw.isdigit():
        private_cat = int(cat_raw)
    return Settings(
        discord_token=token,
        command_prefix=prefix,
        default_poll_interval_sec=max(8, min(poll, 120)),
        data_dir=data_dir,
        private_monitor_enabled=_env_bool("PRIVATE_MONITOR_ENABLED", True),
        private_monitor_category_id=private_cat,
        fast_assistant_enabled=_env_bool("FAST_ASSISTANT_ENABLED", True),
        fast_assistant_sound_enabled=_env_bool("FAST_ASSISTANT_SOUND", False),
        fast_assistant_sound_path=str(Path(sound_path)) if sound_path else "",
        fast_assistant_max_per_minute=max(1, min(max_pm, 30)),
        fast_assistant_max_parallel_tabs=max(1, min(max_par, 6)),
        fast_assistant_min_interval_sec=max(0.25, min(min_iv, 30.0)),
    )
