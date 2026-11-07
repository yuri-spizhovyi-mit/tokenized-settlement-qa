"""UI-09, UI-10: the page's hard-coded constants and its amount arithmetic.

The page has no ABI library: it encodes `transfer` and decodes custom errors by
selector. Those selectors are checked here against `forge build` output, so a
renamed function or error fails a test instead of silently breaking the page.
"""

from __future__ import annotations

import json
import re

import allure
import pytest
from eth_utils import keccak

from lib.artifacts import OUT_DIR, REPO_ROOT

pytestmark = allure.feature("UI: contract binding")

APP_JS = (REPO_ROOT / "ui" / "app.js").read_text()


def _artifact() -> dict:
    return json.loads((OUT_DIR / "SettlementToken.sol" / "SettlementToken.json").read_text())


@pytest.mark.req("UI-09")
def test_function_selectors_match_the_forge_artifact():
    """UI-09: every function selector in app.js equals the compiler's methodIdentifiers."""
    block = re.search(r"export const SELECTORS = \{(.*?)\};", APP_JS, re.S).group(1)
    entries = re.findall(r'(\w+): "0x([0-9a-f]{8})",\s*// (\S+)', block)  # key, selector, signature
    methods = _artifact()["methodIdentifiers"]
    assert {key for key, _, _ in entries} == {"transfer", "balanceOf", "decimals", "symbol"}
    for key, selector, signature in entries:
        assert signature.startswith(key + "(")
        assert methods[signature] == selector, f"{key}: page {selector}, contract {methods[signature]}"


@pytest.mark.req("UI-09")
def test_error_selectors_and_argument_types_match_the_forge_artifact():
    """UI-09: every custom error the page decodes exists in the token's ABI with the
    same selector and the same argument types."""
    errors = {
        e["name"]: [i["type"] for i in e["inputs"]]
        for e in _artifact()["abi"] if e["type"] == "error"
    }
    in_page = re.findall(r'"0x([0-9a-f]{8})": \{ name: "(\w+)", args: \[([^\]]*)\] \}', APP_JS)
    assert len(in_page) == 4
    for selector, name, args in in_page:
        types = re.findall(r'"(\w+)"', args)
        assert errors[name] == types, name
        assert keccak(text=f"{name}({','.join(types)})")[:4].hex() == selector, name


@pytest.mark.req("UI-10")
@pytest.mark.parametrize("base_units, text", [
    (1, "0.000001"),
    (1_000_000, "1"),
    (125_500_000, "125.5"),
    (2**53 + 1, "9,007,199,254.740993"),
    (2**256 - 1, None),  # checked by round trip only
])
def test_amounts_round_trip_exactly_in_the_browser(page, ui_url, base_units, text):
    """UI-10: parseUnits/formatUnits run in the real browser with BigInt: formatting
    then parsing returns exactly the same base units, up to 2^256 - 1."""
    page.goto(f"{ui_url}/index.html")
    result = page.evaluate(
        """async (units) => {
            const m = await import('./app.js');
            const shown = m.formatUnits(BigInt(units), 6);
            return { shown, back: m.parseUnits(shown.replaceAll(',', ''), 6).toString() };
        }""",
        str(base_units),
    )
    assert result["back"] == str(base_units)
    if text is not None:
        assert result["shown"] == text
