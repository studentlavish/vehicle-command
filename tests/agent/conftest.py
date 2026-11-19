"""Agent test harness — runs with local_agent/.venv (no models or cameras needed)."""
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[2] / "local_agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))
