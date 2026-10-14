"""The feed page as it is served: one HTML document holding every stylesheet and script, and loading nothing."""

from __future__ import annotations

import re

import pytest

from ucla_agent.feed.page import PAGE_FOLDER, page_html

PANEL_SLOTS = ("header", "throughput", "choices", "plans", "activity", "status")
# Anything that makes a browser fetch: an address with a scheme or a protocol-relative one, a source or link
# attribute, a stylesheet import or a CSS url().
FETCHING = re.compile(r"https?:|//[A-Za-z0-9.-]+\.[A-Za-z]|\bsrc\s*=|<link\b|@import|url\(", re.IGNORECASE)


@pytest.fixture(scope="module")
def page() -> str:
    return page_html().decode("utf-8")


def test_the_page_is_one_html_document_with_one_style_and_one_script_element(page):
    assert page.startswith("<!doctype html>")
    assert [page.count(tag) for tag in ("<html", "</html>", "<style>", "</style>", "<script>", "</script>")] == [1] * 6


def test_the_page_lays_out_every_panel_slot_and_the_status_line(page):
    for slot in PANEL_SLOTS:
        assert page.count(f'id="{slot}"') == 1, slot


def test_every_stylesheet_and_every_script_of_the_page_folder_is_written_into_the_page(page):
    files = [path for path in PAGE_FOLDER.iterdir() if path.suffix in (".css", ".js")]

    assert {path.name for path in files} >= {"base.css", "feed.js"}
    for path in files:
        assert path.read_text(encoding="utf-8") in page, path.name
    assert "/* styles */" not in page and "/* scripts */" not in page


def test_the_feed_script_comes_before_the_panels_that_register_with_it_and_the_start_comes_last(page):
    script = page[page.index("<script>") : page.index("</script>")]

    assert script.index("const Feed =") < script.index("Feed.start();")
    assert script.rstrip().endswith("Feed.start();")


def test_the_page_refers_to_no_host_and_fetches_nothing_but_the_event_replies(page):
    assert FETCHING.findall(page) == []
    assert re.findall(r"fetch\(([^,)]*)", page) == ["`/events?${query}`"]


def test_the_page_puts_record_text_in_as_text_only(page):
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page and "document.write" not in page
