"""Real Streamlit CLI with cooperative shutdown, used only by startup tests.

The parent owns stdin. A stop line or EOF asks this interpreter to handle SIGINT
itself, avoiding Windows virtualenv redirector/child PID ambiguity. No listening
shutdown endpoint or application-only test switch is added to CF-Stitch.
"""

import _thread
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from streamlit_entry import prepare_runtime, run_streamlit


def wait_for_stop() -> None:
    sys.stdin.readline()
    # A failed graceful shutdown must not leave this test interpreter running.
    fallback = threading.Timer(8, lambda: os._exit(3))
    fallback.daemon = True
    fallback.start()
    _thread.interrupt_main()


prepare_runtime()
threading.Thread(target=wait_for_stop, daemon=True).start()
run_streamlit()
