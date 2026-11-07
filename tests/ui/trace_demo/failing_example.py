"""A deliberately failing UI check, run only by test_trace_on_failure.py (UI-11).

Not collected by the normal suite: its name doesn't start with `test_`.
"""

from playwright.sync_api import expect


def test_deliberately_fails(page, ui_url):
    page.goto(f"{ui_url}/index.html")
    expect(page.get_by_role("button", name="Connect wallet")).to_have_text("This text is not on the page", timeout=500)
