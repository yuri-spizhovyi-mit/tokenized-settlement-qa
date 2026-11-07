"""Requirements traceability matrix (book Ch 13): requirement -> tests, from the code.

    python scripts/rtm.py                 # write docs/rtm.csv and docs/rtm.md; exit 1 on a gap
    python scripts/rtm.py --check         # verify only; CI runs this
    python scripts/rtm.py --dump          # every cited ID with its tests (for writing statements)

Where the links come from (never a hand-kept list):
- **Solidity** (`contracts/test/**/*.t.sol`): the requirement IDs in a test function's own
  NatSpec (`/// @notice ...` above it). A test with none inherits the IDs in its
  contract's NatSpec.
- **Python** (`tests/**/test_*.py`): `@pytest.mark.req("ID", ...)` on the function, or a
  module-level `pytestmark` containing `pytest.mark.req(...)`.

The catalogue (`docs/requirements.csv`) states what each ID means. The check fails if a
catalogued requirement has no test, or if a test cites an ID that isn't catalogued.
"""

from __future__ import annotations

import argparse
import ast
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "docs" / "requirements.csv"
RTM_CSV = ROOT / "docs" / "rtm.csv"
RTM_MD = ROOT / "docs" / "rtm.md"
ID = re.compile(r"\b(?:TOK|ESC|PROP|SEC|INT|IDX|REC|TAPI|LOAD|UI)-\d{2}\b")
# NatSpec shorthand "TOK-02/03/04" means TOK-02, TOK-03 and TOK-04.
ID_SHORTHAND = re.compile(r"\b(TOK|ESC|PROP|SEC|INT|IDX|REC|TAPI|LOAD|UI)-(\d{2})((?:/\d{2})+)")


def ids_in(text: str) -> set[str]:
    found = set(ID.findall(text))
    for prefix, first, rest in ID_SHORTHAND.findall(text):
        found |= {f"{prefix}-{n}" for n in [first, *rest.strip("/").split("/")]}
    return found
SOL_FN = re.compile(r"^\s*function\s+((?:test|testFuzz|invariant)\w*)\s*\(", re.M)
SOL_CONTRACT = re.compile(r"^\s*(?:abstract\s+)?contract\s+(\w+)", re.M)

LEVELS = [  # path prefix -> test level shown in the matrix
    ("contracts/test/unit/", "unit"), ("contracts/test/fuzz/", "fuzz"),
    ("contracts/test/invariant/", "invariant"), ("contracts/test/fork/", "fork"),
    ("contracts/test/security/", "security"), ("tests/integration/", "integration"),
    ("tests/indexing/", "indexer"), ("tests/reconciliation/", "reconciliation"),
    ("tests/transfers/", "api"), ("tests/load/", "load"), ("tests/ui/", "ui"),
]


def level_of(path: str) -> str:
    return next((name for prefix, name in LEVELS if path.startswith(prefix)), "other")


def _natspec_above(lines: list[str], index: int) -> str:
    """The contiguous `///` / `//` comment block directly above line `index`."""
    block = []
    i = index - 1
    while i >= 0 and lines[i].strip().startswith("//"):
        block.append(lines[i])
        i -= 1
    return "\n".join(reversed(block))


def solidity_links() -> dict[str, set[str]]:
    links: dict[str, set[str]] = defaultdict(set)
    for path in sorted((ROOT / "contracts" / "test").rglob("*.t.sol")):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text()
        lines = text.splitlines()
        contract_ids: set[str] = set()
        contract_name = ""
        for n, line in enumerate(lines):
            m = SOL_CONTRACT.match(line)
            if m:
                contract_name = m.group(1)
                contract_ids = ids_in(_natspec_above(lines, n))
            m = SOL_FN.match(line)
            if m:
                ids = ids_in(_natspec_above(lines, n)) or contract_ids
                for rid in ids:
                    links[rid].add(f"{rel}::{contract_name}.{m.group(1)}")
    return links


def _req_ids(node: ast.AST) -> set[str]:
    """IDs from `pytest.mark.req("A", "B")` anywhere inside `node`."""
    found = set()
    for call in ast.walk(node):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "req"):
            found |= {a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return found


def python_links() -> dict[str, set[str]]:
    links: dict[str, set[str]] = defaultdict(set)
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text())
        module_ids: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in node.targets):
                module_ids |= _req_ids(node.value)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                ids = set().union(*(_req_ids(d) for d in node.decorator_list)) | module_ids
                for rid in ids:
                    links[rid].add(f"{rel}::{node.name}")
    return links


def collect() -> dict[str, set[str]]:
    links: dict[str, set[str]] = defaultdict(set)
    for source in (solidity_links(), python_links()):
        for rid, tests in source.items():
            links[rid] |= tests
    return links


def load_catalogue() -> list[dict]:
    with CATALOGUE.open() as f:
        return list(csv.DictReader(f))


def build(links: dict[str, set[str]], catalogue: list[dict]) -> tuple[list[dict], list[str]]:
    rows, problems = [], []
    known = {r["id"] for r in catalogue}
    for req in catalogue:
        tests = sorted(links.get(req["id"], ()))
        levels = sorted({level_of(t) for t in tests})
        if not tests:
            problems.append(f"{req['id']}: catalogued but no test cites it")
        rows.append({**req, "tests": len(tests), "levels": " ".join(levels), "test_ids": "; ".join(tests)})
    for rid in sorted(set(links) - known):
        problems.append(f"{rid}: cited by {len(links[rid])} test(s) but not in docs/requirements.csv")
    if len(known) != len(catalogue):
        problems.append("docs/requirements.csv has duplicate ids")
    return rows, problems


def write(rows: list[dict]) -> None:
    fields = ["id", "area", "risk", "requirement", "tests", "levels", "test_ids"]
    with RTM_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    by_area: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_area[r["area"]].append(r)
    total_links = sum(r["tests"] for r in rows)
    out = [
        "# Requirements traceability matrix",
        "",
        "Generated by `scripts/rtm.py` from the tests themselves (Solidity NatSpec and",
        "`@pytest.mark.req`); do not edit by hand. CI fails if a requirement has no test or a",
        "test cites an unknown requirement. Full test lists: [`rtm.csv`](rtm.csv).",
        "",
        f"**{len(rows)} requirements, every one traced: {total_links} requirement-test links.**",
        "",
        "| Area | Requirements | Links | High-risk |",
        "|---|---|---|---|",
        *[f"| {area} | {len(rs)} | {sum(r['tests'] for r in rs)} | {sum(r['risk'] == 'high' for r in rs)} |"
          for area, rs in by_area.items()],
        "",
    ]
    for area, rs in by_area.items():
        out += [f"## {area}", "", "| ID | Risk | Requirement | Tests | Levels |", "|---|---|---|---|---|"]
        out += [f"| {r['id']} | {r['risk']} | {r['requirement']} | {r['tests']} | {r['levels']} |" for r in rs]
        out.append("")
    RTM_MD.write_text("\n".join(out))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify only; don't write files")
    parser.add_argument("--dump", action="store_true", help="print every cited ID and its tests")
    args = parser.parse_args()
    links = collect()
    if args.dump:
        for rid in sorted(links, key=lambda r: (r.split("-")[0], int(r.split("-")[1]))):
            print(rid, len(links[rid]))
            for t in sorted(links[rid]):
                print("   ", t)
        return
    rows, problems = build(links, load_catalogue())
    if not args.check:
        write(rows)
    for p in problems:
        print(f"RTM: {p}", file=sys.stderr)
    covered = sum(1 for r in rows if r["tests"])
    print(f"RTM: {covered}/{len(rows)} requirements traced to tests, {sum(r['tests'] for r in rows)} links")
    if problems:
        sys.exit(1)
    if args.check:
        stale = RTM_CSV.exists() and RTM_CSV.read_text() != _render_csv(rows)
        if stale:
            print("RTM: docs/rtm.csv is out of date; run `python scripts/rtm.py`", file=sys.stderr)
            sys.exit(1)


def _render_csv(rows: list[dict]) -> str:
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["id", "area", "risk", "requirement", "tests", "levels", "test_ids"],
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


if __name__ == "__main__":
    main()
