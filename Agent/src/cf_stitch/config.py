"""Local-only configuration. This module never reads credentials or calls a network."""

from dataclasses import dataclass
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOST = "127.0.0.1"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    db_path: Path
    host: str = LOCAL_HOST
    port: int = 8502


def load_settings() -> Settings:
    raw_dir = Path(os.environ.get("CF_STITCH_DATA_DIR", "data"))
    data_dir = (PROJECT_ROOT / raw_dir).resolve()
    if not data_dir.is_relative_to(PROJECT_ROOT) or data_dir == PROJECT_ROOT:
        raise ValueError("数据目录必须位于本 CF-Stitch 项目的子目录内。")
    # Never permit configuration to turn the source/spec directory into a data store.
    relative = data_dir.relative_to(PROJECT_ROOT)
    if relative.parts[0].casefold() not in {"data", "artifacts"}:
        raise ValueError("数据目录只能位于本项目 data 或 artifacts 子目录内。")
    try:
        port = int(os.environ.get("CF_STITCH_PORT", "8502"))
    except ValueError as exc:
        raise ValueError("CF_STITCH_PORT 必须是整数端口。") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("CF_STITCH_PORT 必须在 1024–65535 之间。")
    return Settings(PROJECT_ROOT, data_dir, data_dir / "cf_stitch.sqlite3", port=port)

