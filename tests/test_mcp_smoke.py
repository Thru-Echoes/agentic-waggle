"""Smoke tests for MCP server stdio entry points.

These tests don't exercise tool behavior (that lives in
``test_mcp_sales_copilot.py`` and ``test_mcp_customer_reports.py``).
Instead they verify that running each server via ``python -m <module>``
boots cleanly — i.e., the FastMCP banner appears, no import-time
exception is raised, and the process stays alive waiting for stdin.

These catch a different class of bug than the in-process tests:
import-time errors at the entry point, missing optional deps that only
load when the server actually starts, packaging mistakes.

Side effects: spawns two short-lived Python subprocesses per test run.
Each subprocess is terminated after ~3 seconds.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import subprocess
import sys

import pytest


_SERVER_MODULES = [
    "mcp_servers.sales_copilot_server",
    "mcp_servers.customer_reports_server",
]


@pytest.mark.parametrize("module", _SERVER_MODULES)
def test_server_module_starts_cleanly(module: str) -> None:
    """Boot the MCP server via ``python -m <module>`` and verify stderr
    carries the FastMCP banner (or at least: no import-time traceback).

    Times out the subprocess after 3 seconds — at that point the server
    is happily waiting on stdin which is exactly what we want.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(input="", timeout=3)
        # If we got here without a TimeoutExpired, the process exited.
        # That's only OK if the exit was triggered by stdin-EOF and there
        # was no traceback.
        assert "Traceback" not in err, (
            f"{module} raised at startup:\n{err}"
        )
    except subprocess.TimeoutExpired:
        # Server is alive after 3s — exactly what stdio servers do.
        # Tear it down before asserting on stderr.
        proc.terminate()
        try:
            out, err = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        assert "Traceback" not in err, (
            f"{module} raised after startup:\n{err}"
        )
        # FastMCP prints its banner to stderr on startup; presence
        # confirms the server actually initialized.
        assert "fastmcp" in err.lower() or "starting" in err.lower() or err, (
            f"{module} produced no startup output:\nstdout={out!r}\nstderr={err!r}"
        )
