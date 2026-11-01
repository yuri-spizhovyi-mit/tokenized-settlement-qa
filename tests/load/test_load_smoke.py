"""LOAD-01..LOAD-03: the load rig itself works, on every PR (book Ch 11).

The nightly job runs the `nightly` profile (40 users, 3 minutes) and publishes the
report. This smoke run (8 users, 15 s) keeps the rig from rotting between nights,
and checks that correctness holds under concurrent load, not only in unit-sized
tests. It runs the same orchestrator, in subprocesses (Locust needs its own process
for gevent).
"""

from __future__ import annotations

import os
import sys

import allure
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import run_load  # noqa: E402

pytestmark = [allure.feature("Load"), pytest.mark.timeout(400)]


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    out = tmp_path_factory.mktemp("load")
    summary = run_load.run("smoke", out, os.environ.get("INDEXER_DATABASE_URL"))
    # Every assertion below carries the whole report, so a CI failure explains itself.
    _REPORT["text"] = (out / "report.md").read_text()
    return summary, out


_REPORT: dict[str, str] = {}


def _why() -> str:
    return "\n" + _REPORT.get("text", "(no report)")


@pytest.mark.req("LOAD-01")
def test_smoke_run_exercises_every_user_type_without_failures(smoke):
    """LOAD-01: all four Locust user types sent traffic, and no request failed."""
    summary, out = smoke
    by_name = {r["name"]: r for r in summary["requests"]}
    for name in ("POST /transfers", "GET /transfers/{id}", "eth_sendRawTransaction", "eth_call balanceOf"):
        assert by_name[name]["count"] > 0, name + _why()
        assert by_name[name]["failures"] == 0, name + _why()
    assert (out / "report.md").read_text().endswith("**Overall: PASS**\n"), _why()


@pytest.mark.req("LOAD-02")
def test_every_transfer_accepted_under_load_is_confirmed_and_paid_exactly(smoke):
    """LOAD-02: after the drain, every transfer the API accepted is confirmed, and the
    API's confirmed total equals what the hot wallet actually paid on chain.

    The reference is the database and the chain, not Locust's request count: when the
    run's time is up, Locust stops users mid-request, so a transfer can be committed
    whose response it never records (seen once: 184 transfers, 183 POSTs counted)."""
    summary, _ = smoke
    results = summary["results"]
    posts = next(r["count"] for r in summary["requests"] if r["name"] == "POST /transfers")
    assert results["drained"], _why()
    assert set(results["transfers_by_status"]) == {"confirmed"}, _why()
    assert 0 < results["transfers_by_status"]["confirmed"] <= posts + summary["config"]["users"], _why()
    assert results["api_confirmed_amount"] == results["hot_wallet_spent_on_chain"], _why()
    assert results["worker"]["errors"] == 0, _why()


@pytest.mark.req("LOAD-03")
def test_indexer_keeps_up_and_reconciles_after_load(smoke):
    """LOAD-03: the indexer's lag stays within a few blocks during load, and after it
    the database reconciles with the chain (balances, supply, escrow) at its tip."""
    summary, _ = smoke
    assert summary["results"]["reconciliation_mismatches"] == [], _why()
    assert summary["indexer_lag_blocks"]["max"] <= 3, _why()
    assert summary["results"]["indexer"]["errors"] == 0, _why()
