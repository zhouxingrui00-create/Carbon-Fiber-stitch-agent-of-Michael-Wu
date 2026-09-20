"""Initialize table libraries on the main thread before starting Streamlit.

A real Windows browser session exposed concurrent NumPy native-module loading
and server-worker startup hanging at the first dataframe. Loading the existing
Streamlit table dependencies before server threads avoids that first-use race.
This changes only process startup, not installed packages or system settings.
"""

from __future__ import annotations

import runpy
import sys
import threading


def prepare_runtime() -> None:
    """Complete native table imports synchronously in the process main thread."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("表格运行环境必须在服务启动前由主线程初始化。")
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401


def run_streamlit(argv: list[str] | None = None) -> None:
    """Run the real CLI after the caller has prepared its runtime."""
    sys.argv = ["streamlit", *(sys.argv[1:] if argv is None else argv)]
    runpy.run_module("streamlit", run_name="__main__", alter_sys=False)


def main(argv: list[str] | None = None) -> None:
    prepare_runtime()
    run_streamlit(argv)


if __name__ == "__main__":
    main()
