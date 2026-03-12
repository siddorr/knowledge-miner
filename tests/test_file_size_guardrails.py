from __future__ import annotations

import subprocess
from pathlib import Path


def test_file_size_guardrails_have_no_unapproved_failures():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["python", "scripts/check_file_sizes.py"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
