"""Startup checks use the actual local runtime, including one owned HTTP process."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request

import pytest

from cf_stitch.config import PROJECT_ROOT, load_settings

spec = importlib.util.spec_from_file_location("cf_stitch_launch", PROJECT_ROOT / "scripts" / "launch.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


def test_local_defaults_and_no_side_effects(monkeypatch):
    monkeypatch.delenv("CF_STITCH_PORT", raising=False)
    monkeypatch.delenv("CF_STITCH_DATA_DIR", raising=False)
    settings = load_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8502
    assert settings.db_path == PROJECT_ROOT / "data" / "cf_stitch.sqlite3"


@pytest.mark.parametrize("value", ["../welding", str(PROJECT_ROOT.parent), ".", "sources", "spec/local"])
def test_outside_and_source_data_paths_rejected(monkeypatch, value):
    monkeypatch.setenv("CF_STITCH_DATA_DIR", value)
    with pytest.raises(ValueError):
        load_settings()


@pytest.mark.parametrize("value", ["0", "80", "65536", "not-a-port"])
def test_invalid_ports_rejected(monkeypatch, value):
    monkeypatch.setenv("CF_STITCH_PORT", value)
    with pytest.raises(ValueError):
        load_settings()


def test_command_line_enforces_local_privacy(monkeypatch):
    monkeypatch.setenv("STREAMLIT_SERVER_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "true")
    command = launch.build_command(load_settings())
    assert command[0] == sys.executable
    assert command[command.index("--server.address") + 1] == "127.0.0.1"
    assert command[command.index("--browser.gatherUsageStats") + 1] == "false"
    assert command[command.index("--server.headless") + 1] == "true"
    assert command[command.index("--server.enableXsrfProtection") + 1] == "true"
    assert command[command.index("--client.toolbarMode") + 1] == "viewer"
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["address"] == "127.0.0.1"
    assert config["browser"]["gatherUsageStats"] is False
    assert config["client"]["toolbarMode"] == "viewer"


def test_busy_port_reports_and_preserves_listener(monkeypatch, capsys):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setenv("CF_STITCH_PORT", str(port))
        assert launch.main(["--check"]) == 2
        assert "不会停止现有程序" in capsys.readouterr().err
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass


@pytest.mark.skipif(os.name != "nt", reason="Windows bat launcher")
def test_windows_start_bat_check():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "CF_STITCH_PORT": str(port), "PYTHONUTF8": "1"}
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "start.bat", "--check"], cwd=PROJECT_ROOT,
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"127.0.0.1:{port}" in result.stdout


def test_real_http_startup_and_shutdown(tmp_path, monkeypatch):
    """Health + HTML checks are NOT a claim that browser interactions were tested."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("CF_STITCH_PORT", str(port))
    settings = load_settings()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    logfile = tmp_path / "streamlit-startup.log"
    env = {**os.environ, "PYTHONUTF8": "1"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # Same real CLI arguments, with an stdin-owned test shutdown wrapper.
    command = [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "tests" / "streamlit_worker.py"),
               *launch.streamlit_arguments(settings)]
    with logfile.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command, cwd=PROJECT_ROOT, env=env, stdin=subprocess.PIPE,
            stdout=log, stderr=subprocess.STDOUT, creationflags=flags, text=True,
        )
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail(logfile.read_text(encoding="utf-8"))
                try:
                    with opener.open(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                        assert response.status == 200
                        assert response.read().strip() == b"ok"
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                pytest.fail("Streamlit did not become healthy within 45 seconds")
            with opener.open(f"http://127.0.0.1:{port}/", timeout=3) as response:
                assert response.status == 200
                assert b"<html" in response.read().lower()
        finally:
            # Only the process with this stdin pipe receives the stop request.
            if proc.poll() is None:
                try:
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
                except BrokenPipeError:
                    pass
            proc.wait(timeout=15)
            proc.stdin.close()
    assert proc.returncode == 0, logfile.read_text(encoding="utf-8")
    # Assert the interpreter child did not survive its virtualenv redirector.
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_real_entry_prepares_native_tables_before_streamlit_in_fresh_process(tmp_path):
    """Observe real imports and a native table roundtrip, outside AppTest caches."""
    harness = tmp_path / "entry-import-audit.py"
    harness.write_text('''import json
from pathlib import Path
import runpy
import sys
import threading

observed = []
def audit(event, arguments):
    if event == "import" and arguments[0] in ("numpy", "pandas", "pyarrow", "streamlit"):
        observed.append([arguments[0], threading.current_thread() is threading.main_thread()])
sys.addaudithook(audit)
entry = Path(sys.argv[1])
sys.argv = [str(entry), "--version"]
try:
    runpy.run_path(str(entry), run_name="__main__")
except SystemExit as exc:
    if exc.code not in (None, 0):
        raise
import pandas as pd
import pyarrow as pa
finished = []
def table_roundtrip():
    frame = pd.DataFrame({"参数": ["针距", "供纱张力"], "值": [3.0, 0.5]})
    finished.append(pa.Table.from_pandas(frame).to_pandas().to_dict("list"))
worker = threading.Thread(target=table_roundtrip, daemon=True)
worker.start()
worker.join(10)
if worker.is_alive():
    raise RuntimeError("表格线程未能完成")
print("CF_STITCH_IMPORT_AUDIT=" + json.dumps({"imports": observed, "table": finished}, ensure_ascii=False))
''', encoding="utf-8")
    entry = PROJECT_ROOT / "scripts" / "streamlit_entry.py"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(harness), str(entry)], cwd=PROJECT_ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    audit_line = next(line for line in result.stdout.splitlines() if line.startswith("CF_STITCH_IMPORT_AUDIT="))
    report = json.loads(audit_line.split("=", 1)[1])
    imports = report["imports"]
    names = [item[0] for item in imports]
    assert {"numpy", "pandas", "pyarrow", "streamlit"}.issubset(names)
    assert all(is_main for name, is_main in imports)
    assert max(names.index(name) for name in ("numpy", "pandas", "pyarrow")) < names.index("streamlit")
    assert report["table"] == [{"参数": ["针距", "供纱张力"], "值": [3.0, 0.5]}]
