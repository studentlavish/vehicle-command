"""Repository hygiene: no test leftovers, debug code or leaked secrets in app code.

Pure standard library — runs under either virtualenv.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIRS = ("backend", "local_agent", "frontend/src")
TEXT_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".sh", ".bat", ".yml", ".yaml", ".html", ".css", ".txt", ".md"}


def _tracked(*paths: str) -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--", *paths], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.splitlines() if p]


def _app_files() -> list[Path]:
    return [p for p in _tracked(*APP_DIRS) if p.suffix in TEXT_EXT and p.is_file()]


def _hits(pattern: str, files: list[Path]) -> list[str]:
    rx = re.compile(pattern)
    found = []
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if rx.search(line):
                found.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    return found


def test_no_leaked_secrets_or_preview_urls():
    files = _app_files() + [p for p in _tracked(".") if p.suffix in {".md", ".sh"} and p.is_file()]
    assert not _hits(r"vashu-agent-secret-change-me|preview\.emergentagent\.com", files)


def test_no_hardcoded_default_passwords_outside_weak_list():
    hits = [h for h in _hits(r"""["']admin123["']|["']demo1234["']""", _app_files()) if "WEAK_PASSWORDS" not in h]
    assert not hits, hits


def test_no_debugger_statements():
    assert not _hits(r"\bbreakpoint\(\)|pdb\.set_trace\(|^\s*debugger;", _app_files())


def test_no_print_debugging_in_backend():
    """Server modules log; only command-line tools (with a __main__ block) may print."""
    py = [p for p in _app_files() if p.suffix == ".py" and p.parts[len(ROOT.parts)] == "backend"
          and 'if __name__ == "__main__"' not in p.read_text(encoding="utf-8")]
    assert not _hits(r"^\s*print\(", py)


def test_no_console_logging_in_frontend():
    js = [p for p in _app_files() if p.suffix in {".js", ".jsx", ".ts", ".tsx"}]
    assert not _hits(r"console\.(log|debug)\(", js)


def test_no_synthetic_test_plates_in_app_code():
    assert not _hits(r"\bTEST[0-9A-Z]{3,}\b", _app_files())


def test_no_tracked_runtime_data_or_reports():
    tracked = {str(p.relative_to(ROOT)) for p in _tracked(".")}
    assert {p for p in tracked if p.startswith("backend/snapshots/")} <= {"backend/snapshots/.gitkeep"}
    assert not {p for p in tracked if p.startswith(("test_reports/", "logs/"))}
    assert not {p for p in tracked if Path(p).name == ".env" or Path(p).name.endswith(".env")}
    assert "test_result.md" not in tracked


def test_no_tests_outside_tests_folder():
    stray = [p for p in _tracked(*APP_DIRS) if re.match(r"(test_.*|.*_test)\.py$", p.name)]
    assert not stray, stray


def test_plate_rules_copies_identical():
    a = (ROOT / "backend" / "plate_rules.py").read_bytes()
    b = (ROOT / "local_agent" / "plate_rules.py").read_bytes()
    assert a == b, "backend/plate_rules.py and local_agent/plate_rules.py have drifted"
