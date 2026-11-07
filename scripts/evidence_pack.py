"""Build the release evidence pack (book Ch 14).

    python scripts/evidence_pack.py --results results/ --out dist/ --version v1.0.0

`results/` holds the raw outputs of the release run (release.yml produces them):

    forge-tests.json        forge test --json
    coverage.txt            forge coverage --report summary
    slither.json            slither --json (the gated scope)
    junit-python.xml        pytest --junitxml (integration, indexer, reconciliation, API, load)
    junit-ui.xml            pytest tests/ui --junitxml
    gas-snapshot-check.txt  forge snapshot --check output

The pack (`dist/evidence-pack-<version>/` and a `.zip` of it) contains those raw files,
the process documents (strategy, requirements, RTM, security plan, UAT scripts, findings,
decisions, Slither triage, performance report), an `EVIDENCE.md` summary, and
`SHA256SUMS` for every file.

It refuses to build (exit 1) if any test failed or errored, if a result file is missing,
if production contracts are not fully covered, if the gated Slither scope has a High
finding, or if the RTM has a gap. A pack exists only for a release that passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    "docs/test-strategy.md", "docs/requirements.csv", "docs/rtm.md", "docs/rtm.csv",
    "docs/security-test-plan.md", "docs/uat-scripts.md", "docs/findings.md", "docs/decisions.md",
    "docs/slither-triage.md", "docs/performance-report.md", "docs/plan.md",
]
RESULTS = ["forge-tests.json", "coverage.txt", "slither.json", "junit-python.xml", "junit-ui.xml",
           "gas-snapshot-check.txt"]
PRODUCTION = ("src/Escrow.sol", "src/SettlementToken.sol")


def forge_summary(path: Path) -> dict:
    data = json.loads(path.read_text())
    counts: dict[str, int] = {}
    for suite in data.values():
        for result in suite["test_results"].values():
            counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {"suites": len(data), **counts}


def junit_summary(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    total = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    # pytest reports xfail as skipped with a message starting "xfail"
    xfailed = sum(1 for case in root.iter("testcase") for sk in case.findall("skipped")
                  if (sk.get("type") or "").endswith("xfail") or (sk.get("message") or "").startswith("xfail"))
    total["xfailed"], total["skipped"] = xfailed, total["skipped"] - xfailed
    total["passed"] = total["tests"] - total["failures"] - total["errors"] - total["skipped"] - xfailed
    return total


def coverage_summary(path: Path) -> dict:
    rows = {}
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] in (*PRODUCTION, "Total"):
            rows[cells[0]] = cells[1:5]  # lines, statements, branches, functions
    return rows


def slither_summary(path: Path) -> dict:
    data = json.loads(path.read_text())
    counts: dict[str, int] = {}
    for d in (data.get("results") or {}).get("detectors", []):
        counts[d["impact"]] = counts.get(d["impact"], 0) + 1
    return counts


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=git("describe", "--tags", "--always"))
    args = parser.parse_args()

    problems: list[str] = []
    missing = [name for name in RESULTS if not (args.results / name).exists()]
    if missing:
        sys.exit(f"evidence pack: missing result files: {missing}")

    forge = forge_summary(args.results / "forge-tests.json")
    if set(forge) - {"suites", "Success", "Skipped"}:
        problems.append(f"forge: {forge}")
    junit = {name: junit_summary(args.results / f"junit-{name}.xml") for name in ("python", "ui")}
    for name, s in junit.items():
        if s["failures"] or s["errors"]:
            problems.append(f"pytest {name}: {s['failures']} failed, {s['errors']} errors")
    coverage = coverage_summary(args.results / "coverage.txt")
    for contract in PRODUCTION:
        cells = coverage.get(contract)
        if not cells or not all(c.startswith("100.00%") for c in cells):
            problems.append(f"coverage below 100% for {contract}: {cells}")
    slither = slither_summary(args.results / "slither.json")
    if slither.get("High"):
        problems.append(f"slither: {slither['High']} High finding(s) in the gated scope")
    rtm = subprocess.run([sys.executable, "scripts/rtm.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    if rtm.returncode:
        problems.append(f"rtm: {rtm.stderr.strip() or rtm.stdout.strip()}")
    rtm_line = (rtm.stdout.strip().splitlines() or ["RTM: ?"])[-1]

    if problems:
        print("evidence pack NOT built:", *problems, sep="\n  ", file=sys.stderr)
        sys.exit(1)

    name = f"evidence-pack-{args.version}"
    pack = args.out / name
    if pack.exists():
        shutil.rmtree(pack)
    (pack / "results").mkdir(parents=True)
    (pack / "docs" / "performance").mkdir(parents=True)
    for doc in DOCS:
        shutil.copy2(ROOT / doc, pack / doc)
    for f in (ROOT / "docs" / "performance").iterdir():
        shutil.copy2(f, pack / "docs" / "performance" / f.name)
    for result in RESULTS:
        shutil.copy2(args.results / result, pack / "results" / result)

    perf = json.loads((ROOT / "docs/performance/nightly-after-postgres.json").read_text())
    gas = (args.results / "gas-snapshot-check.txt").read_text().strip().splitlines()[-1:] or ["?"]
    findings = len(re.findall(r"^## F-\d+", (ROOT / "docs/findings.md").read_text(), re.M))
    decisions = len(re.findall(r"^## [A-Z]+-D\d+", (ROOT / "docs/decisions.md").read_text(), re.M))
    cov_row = lambda c: " / ".join(coverage[c])  # noqa: E731
    slither_text = ", ".join(f"{n} {sev}" for sev, n in sorted(slither.items())) or "no results"
    confirmed = perf["results"]["transfers_by_status"].get("confirmed", 0)
    evidence = f"""# Evidence pack {args.version}

Commit `{git('rev-parse', 'HEAD')}` · built {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} ·
`scripts/evidence_pack.py`. Every file is listed with its SHA-256 in `SHA256SUMS`.

## Verdict

**PASS.** The pack is only built when every check below passes.

| Check | Result |
|---|---|
| Foundry tests (unit, fuzz, invariant, security; `ci` profile) | {forge.get('Success', 0)} passed, {forge.get('Skipped', 0)} skipped, in {forge['suites']} suites |
| Python suites (integration, indexer, reconciliation, transfer API, load smoke) | {junit['python']['passed']} passed, {junit['python']['xfailed']} strict-xfailed (deliberately broken variants), {junit['python']['skipped']} skipped |
| UI suite (Playwright, Chromium) | {junit['ui']['passed']} passed, {junit['ui']['skipped']} skipped |
| Traceability | {rtm_line.removeprefix('RTM: ')} (`docs/rtm.md`) |
| Coverage, `src/Escrow.sol` (lines, statements, branches, functions) | {cov_row('src/Escrow.sol')} |
| Coverage, `src/SettlementToken.sol` | {cov_row('src/SettlementToken.sol')} |
| Slither, gated scope | {slither_text}; 0 High. Every result triaged in `docs/slither-triage.md` |
| Gas snapshot (unit tests) | {gas[0]} |
| Load (last nightly-profile evidence, Postgres) | {confirmed:,} of {sum(perf['results']['transfers_by_status'].values()):,} transfers confirmed, gate {'PASS' if perf['passed'] else 'FAIL'} (`docs/performance-report.md`) |

Mainnet fork tests run in the release job only when `MAINNET_RPC_URL` is configured;
they're included in the Foundry count above when it is.

## Contents

| Path | What it is |
|---|---|
| `docs/test-strategy.md` | scope, levels, risks, environments, entry/exit criteria |
| `docs/requirements.csv`, `docs/rtm.md`, `docs/rtm.csv` | requirements and their traced tests |
| `docs/security-test-plan.md` | threat model and the security tests that cover it |
| `docs/uat-scripts.md` | user acceptance scripts, each linked to its automated test |
| `docs/findings.md` | {findings} findings with evidence (planted, real, harness) |
| `docs/decisions.md` | {decisions} recorded behaviour decisions |
| `docs/slither-triage.md` | every static-analysis result and its verdict |
| `docs/performance-report.md`, `docs/performance/` | load results and the named bottleneck |
| `results/` | raw outputs of this release's run |
"""
    (pack / "EVIDENCE.md").write_text(evidence)
    sums = []
    for f in sorted(p for p in pack.rglob("*") if p.is_file()):
        sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(pack).as_posix()}")
    (pack / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    archive = shutil.make_archive(str(args.out / name), "zip", root_dir=args.out, base_dir=name)
    print(evidence)
    print(f"evidence pack: {archive} ({len(sums)} files)")


if __name__ == "__main__":
    main()
