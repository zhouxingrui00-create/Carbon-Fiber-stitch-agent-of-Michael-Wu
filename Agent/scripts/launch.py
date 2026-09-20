"""Start CF-Stitch using its own interpreter and a local-only listener."""

from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cf_stitch.config import LOCAL_HOST, Settings, load_settings  # noqa: E402


def check_port(port: int) -> None:
    """Check by binding, not by terminating whichever process owns the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((LOCAL_HOST, port))


def streamlit_arguments(settings: Settings) -> list[str]:
    return [
        "run", str(ROOT / "app.py"),
        "--server.address", LOCAL_HOST,
        "--server.port", str(settings.port),
        "--browser.serverAddress", LOCAL_HOST,
        "--browser.serverPort", str(settings.port),
        "--browser.gatherUsageStats", "false",
        "--client.toolbarMode", "viewer",
        "--server.headless", "true",
        "--server.fileWatcherType", "none",
        "--server.enableCORS", "true",
        "--server.enableXsrfProtection", "true",
    ]


def build_command(settings: Settings) -> list[str]:
    return [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "streamlit_entry.py"),
            *streamlit_arguments(settings)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CF-Stitch 本地启动器")
    parser.add_argument("--check", action="store_true", help="只检查环境和端口，不启动")
    args = parser.parse_args(argv)
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        print("请使用本项目 .venv\\Scripts\\python.exe 运行启动器。", file=sys.stderr)
        return 2
    try:
        settings = load_settings()
        versions = {name: importlib.metadata.version(name) for name in ("streamlit", "pydantic", "PyYAML")}
        check_port(settings.port)
    except (ValueError, OSError, importlib.metadata.PackageNotFoundError) as exc:
        print(f"启动检查失败：{exc}", file=sys.stderr)
        print("如端口被占用，请自行选择空闲 CF_STITCH_PORT；不会停止现有程序。", file=sys.stderr)
        return 2
    print(f"CF-Stitch | http://{LOCAL_HOST}:{settings.port} | 数据库：{settings.db_path}", flush=True)
    print(f"独立解释器：{sys.executable} | 依赖：{versions}", flush=True)
    if args.check:
        return 0
    try:
        return subprocess.call(build_command(settings), cwd=ROOT)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
