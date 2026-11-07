"""Check that every relative link in the README, CLAUDE.md and docs/ points at a real file.

    python scripts/check_links.py      # exit 1 on a broken link; CI runs it

Checks Markdown links `[text](target)` whose target is a relative path (http(s),
mailto and in-page `#anchor` links are skipped; a `#fragment` after a path is
ignored). Also checks the `tests/...py::test_name` references in the README: the file
must exist and define that test, because the README sends reviewers to them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "README.md", ROOT / "CLAUDE.md", *sorted((ROOT / "docs").rglob("*.md"))]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TEST_REF = re.compile(r"`(tests/[\w/]+\.py)::(test_\w+)`")


def main() -> None:
    broken = []
    checked = 0
    for md in FILES:
        text = md.read_text()
        in_code = False
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("```"):
                in_code = not in_code
            if in_code:
                continue
            for target in LINK.findall(line):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                checked += 1
                path = (md.parent / target.split("#", 1)[0]).resolve()
                if not path.exists():
                    broken.append(f"{md.relative_to(ROOT)}:{n}: {target}")
            for file, test in TEST_REF.findall(line):
                checked += 1
                source = ROOT / file
                if not source.exists() or not re.search(rf"^def {test}\(", source.read_text(), re.M):
                    broken.append(f"{md.relative_to(ROOT)}:{n}: {file}::{test}")
    for b in broken:
        print(f"broken link: {b}", file=sys.stderr)
    print(f"links: {checked} checked in {len(FILES)} files, {len(broken)} broken")
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
