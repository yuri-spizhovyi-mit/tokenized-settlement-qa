"""UI-11: a failing UI test leaves a Playwright trace behind (the milestone's
"traces on failure").

Runs a deliberately failing check in a child pytest with the same options CI uses,
then opens the trace: it must exist and contain the page's actions and DOM snapshots.
A trace setting that silently stopped working would only be noticed on the day a
real failure needed it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import allure
import pytest

from lib.artifacts import REPO_ROOT

pytestmark = allure.feature("UI: traces")

HERE = Path(__file__).resolve().parent


@pytest.mark.req("UI-11")
def test_failed_ui_test_keeps_a_trace_with_dom_snapshots(tmp_path):
    out = tmp_path / "results"
    child = subprocess.run(
        [sys.executable, "-m", "pytest", str(HERE / "trace_demo" / "failing_example.py"),
         "-p", "no:cacheprovider", "-q", "--rootdir", str(REPO_ROOT),
         "--tracing", "retain-on-failure", "--output", str(out),
         "-o", "python_files=failing_example.py", "--confcutdir", str(REPO_ROOT / "tests")],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300, env={**os.environ},
    )
    assert child.returncode == 1, child.stdout[-2000:] + child.stderr[-2000:]
    assert "1 failed" in child.stdout

    traces = list(out.rglob("trace.zip"))
    assert len(traces) == 1, f"expected one trace under {out}: {list(out.rglob('*'))}"
    with zipfile.ZipFile(traces[0]) as z:
        names = z.namelist()
        trace = z.read(next(n for n in names if n.endswith(".trace"))).decode()
    assert "index.html" in trace                  # the navigation was recorded
    assert "Connect wallet" in trace              # and the page's DOM snapshot
    assert any(n.startswith("resources/") for n in names)
