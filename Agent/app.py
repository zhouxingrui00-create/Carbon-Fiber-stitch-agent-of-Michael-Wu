"""Local Streamlit entry point; resolve only this project's source tree."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from cf_stitch.ui import run

run()
