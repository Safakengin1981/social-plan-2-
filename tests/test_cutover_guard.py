"""Safety gates before enabling PostgreSQL on the real SocialPlan entrypoint."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_main_app_refuses_silent_sqlite_fallback():
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://example.invalid/socialplan"
    result = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert "PostgreSQL cutover is not enabled" in result.stderr
    assert "postgresql://example.invalid" not in result.stderr
