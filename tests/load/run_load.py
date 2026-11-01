"""Run one load test end to end and write a report (book Ch 11).

    .venv/bin/python tests/load/run_load.py --profile smoke|nightly [--out DIR] [--db URL]

1. Start the SUT (sut.py) in its own process; wait for env.json.
2. Run Locust headless for the profile's duration and user count; stats go to CSV.
3. Create the `stop` file: the SUT drains, reconciles, writes results.json.
4. Render `report.md` from Locust's CSV, the SUT's samples and results, and write
   `summary.json`. Exit non-zero if a correctness check failed, so CI goes red.

Load numbers alone pass nothing. A run passes only if every accepted transfer became
final, the API's confirmed total equals what the hot wallet actually paid, and the
indexer reconciles with the chain.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

PROFILES = {
    # users, spawn rate per second, seconds, funded accounts for ChainWriteUser
    "smoke": {"users": 8, "spawn_rate": 8, "seconds": 15, "accounts": 8},
    "nightly": {"users": 40, "spawn_rate": 5, "seconds": 180, "accounts": 40},
}


def run(profile: str, out: Path, db: str | None) -> dict:
    cfg = PROFILES[profile]
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("env.json", "stop", "results.json", "samples.jsonl"):
        (out / stale).unlink(missing_ok=True)
    python = sys.executable

    sut_cmd = [python, str(HERE / "sut.py"), "--out", str(out), "--accounts", str(cfg["accounts"])]
    if db:
        sut_cmd += ["--db", db]
    sut_log = (out / "sut.log").open("w")
    sut = subprocess.Popen(sut_cmd, stdout=sut_log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 180
        while not (out / "env.json").exists():
            if sut.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"SUT did not start; see {out / 'sut.log'}")
            time.sleep(0.25)

        locust_cmd = [
            python, "-m", "locust", "-f", str(HERE / "locustfile.py"), "--headless",
            "-u", str(cfg["users"]), "-r", str(cfg["spawn_rate"]), "-t", f"{cfg['seconds']}s",
            "--csv", str(out / "locust"), "--only-summary", "--stop-timeout", "5",
            "--exit-code-on-error", "0",  # failures are judged in the report, not by Locust
        ]
        env = {**os.environ, "LOAD_ENV": str(out / "env.json")}
        with (out / "locust.log").open("w") as log:
            subprocess.run(locust_cmd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

        (out / "stop").touch()
        sut.wait(timeout=300)
    finally:
        if sut.poll() is None:
            sut.kill()
        sut_log.close()

    summary = summarise(out, profile, cfg)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "report.md").write_text(render(summary))
    return summary


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summarise(out: Path, profile: str, cfg: dict) -> dict:
    results = json.loads((out / "results.json").read_text())
    with (out / "locust_stats.csv").open() as f:
        rows = [r for r in csv.DictReader(f)]
    requests = [
        {
            "type": r["Type"], "name": r["Name"], "count": int(r["Request Count"]),
            "failures": int(r["Failure Count"]), "rps": round(float(r["Requests/s"]), 1),
            "p50_ms": float(r["50%"]), "p95_ms": float(r["95%"]), "p99_ms": float(r["99%"]),
        }
        for r in rows if r["Name"] != "Aggregated"
    ]
    samples = [json.loads(line) for line in (out / "samples.jsonl").read_text().splitlines() if line.strip()]
    samples = [s for s in samples if "error" not in s]
    # Skip warm-up: until the indexer first reaches the head, lag measures setup, not load.
    warm = next((i for i, s in enumerate(samples) if (s.get("lag_blocks") or 0) <= 1), 0)
    samples = samples[warm:]
    lags = [s["lag_blocks"] for s in samples if s.get("lag_blocks") is not None]
    backlog = [sum(v for k, v in s["transfers"].items() if k not in ("confirmed", "failed")) for s in samples]
    confirmed = [s["transfers"].get("confirmed", 0) for s in samples]
    query_ms = [s["balances_query_ms"] for s in samples]
    duration = cfg["seconds"]

    raw = next((r for r in requests if r["name"] == "eth_sendRawTransaction"), {"count": 0})
    refused_503 = next((r["count"] for r in requests if r["name"] == "503 Retry-After"), 0)
    created = sum(results["transfers_by_status"].values())  # POSTs also count 503s and replays
    accepted_per_s = created / duration
    load_samples = [s for s in samples if s["t"] <= duration]
    confirmed_in_load = load_samples[-1]["transfers"].get("confirmed", 0) if load_samples else 0
    checks = {
        "all_transfers_final": results["drained"],
        "api_total_matches_chain": results["api_amount_matches_chain"],
        "indexer_reconciles": not results["reconciliation_mismatches"],
        "no_worker_errors": results["worker"]["errors"] == 0,
        "no_indexer_errors": results["indexer"]["errors"] == 0,
        "no_request_failures": all(r["failures"] == 0 for r in requests),
    }
    return {
        "profile": profile, "config": cfg, "results": results, "requests": requests,
        "throughput": {
            "api_transfers_created": created,
            "api_refused_503": refused_503,
            "api_accepted_per_s": round(accepted_per_s, 1),
            "api_confirmed_per_s_during_load": round(confirmed_in_load / duration, 1),
            "direct_rpc_sends_per_s": round(raw["count"] / duration, 1),
            "worker_busy_fraction": round(results["worker"]["busy_s"] / max(results["load_seconds"] + results["drain_seconds"], 1), 2),
        },
        "backlog": {"max": max(backlog, default=0), "end_of_load": load_samples[-1:] and backlog[len(load_samples) - 1] or 0},
        "indexer_lag_blocks": {"p50": _pct(lags, 0.5), "p95": _pct(lags, 0.95), "max": max(lags, default=None)},
        "balances_query_ms": {
            "first": query_ms[0] if query_ms else None, "last": query_ms[-1] if query_ms else None,
            "median": statistics.median(query_ms) if query_ms else None,
        },
        "confirmed_total": confirmed[-1] if confirmed else 0,
        "checks": checks,
        "passed": all(checks.values()),
    }


def render(s: dict) -> str:
    r, t = s["results"], s["throughput"]
    lines = [
        f"# Load run: {s['profile']} ({s['config']['users']} users, {s['config']['seconds']} s, {r['db']})",
        "",
        "## Correctness after load",
        "",
        "| Check | Result |", "|---|---|",
        *[f"| {k} | {'PASS' if v else '**FAIL**'} |" for k, v in s["checks"].items()],
        "",
        f"Transfers by status: `{r['transfers_by_status']}`; drain took {r['drain_seconds']} s; "
        f"gap fills: {r['gap_fills']}; indexed events: {r['indexed_events']}; "
        f"reconciled at block {r['reconciled_at_block']}.",
        "",
        "## Requests",
        "",
        "| Type | Name | Count | Failures | req/s | p50 ms | p95 ms | p99 ms |",
        "|---|---|---|---|---|---|---|---|",
        *[f"| {q['type']} | {q['name']} | {q['count']} | {q['failures']} | {q['rps']} | {q['p50_ms']:.0f} | "
          f"{q['p95_ms']:.0f} | {q['p99_ms']:.0f} |" for q in s["requests"]],
        "",
        "## Pipeline",
        "",
        "| Metric | Value |", "|---|---|",
        f"| API transfers created (202) / refused with 503 | {t['api_transfers_created']} / {t['api_refused_503']} |",
        f"| API transfers accepted / s | {t['api_accepted_per_s']} |",
        f"| API transfers confirmed / s (during load) | {t['api_confirmed_per_s_during_load']} |",
        f"| Direct RPC sends / s (per-user accounts) | {t['direct_rpc_sends_per_s']} |",
        f"| Worker busy fraction | {t['worker_busy_fraction']} |",
        f"| Transfer backlog (not final): max / at end of load | {s['backlog']['max']} / {s['backlog']['end_of_load']} |",
        f"| Indexer lag, blocks: p50 / p95 / max | {s['indexer_lag_blocks']['p50']} / "
        f"{s['indexer_lag_blocks']['p95']} / {s['indexer_lag_blocks']['max']} |",
        f"| Full-balance query, ms: first / median / last | {s['balances_query_ms']['first']} / "
        f"{s['balances_query_ms']['median']} / {s['balances_query_ms']['last']} |",
        "",
        f"**Overall: {'PASS' if s['passed'] else 'FAIL'}**",
        "",
    ]
    return "\n".join(lines)


def rerender(out: Path) -> dict:
    """Re-summarise an existing results directory (after a change to the summary code)."""
    summary = json.loads((out / "summary.json").read_text())
    summary = summarise(out, summary["profile"], summary["config"])
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "report.md").write_text(render(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="smoke")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "load-results")
    parser.add_argument("--db", default=os.environ.get("INDEXER_DATABASE_URL"))
    args = parser.parse_args()
    summary = run(args.profile, args.out, args.db)
    print((args.out / "report.md").read_text())
    sys.exit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
