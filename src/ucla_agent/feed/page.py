"""The feed page as the server sends it: one HTML document with all its styles and scripts inside.

The page is kept as separate files under `page/`, one stylesheet and one
script for each of its panels, and put together here: every `*.css` file goes
into the shell's one style element and every `*.js` file into its one script
element, `feed.js` first, since every panel registers with the `Feed` it
defines. The document therefore loads nothing from anywhere, this server
included, beyond the event replies its script polls for.
"""

from __future__ import annotations

from pathlib import Path

PAGE_FOLDER = Path(__file__).resolve().parent / "page"
SHELL_FILE = "shell.html"
# The script that defines `Feed`, which every other script registers its panel with.
FEED_SCRIPT = "feed.js"
STYLES_PLACE = "/* styles */"
SCRIPTS_PLACE = "/* scripts */"
_FILE_ENCODING = "utf-8"


def page_html() -> bytes:
    """The whole page: the shell with every stylesheet and every script of the page folder written into it."""
    shell = _text(SHELL_FILE)
    styles = [_text(path.name) for path in sorted(PAGE_FOLDER.glob("*.css"))]
    panel_scripts = sorted(path.name for path in PAGE_FOLDER.glob("*.js") if path.name != FEED_SCRIPT)
    scripts = [_text(name) for name in (FEED_SCRIPT, *panel_scripts)]
    page = shell.replace(STYLES_PLACE, "\n".join(styles)).replace(SCRIPTS_PLACE, "\n".join(scripts))
    return page.encode(_FILE_ENCODING)


def _text(name: str) -> str:
    return (PAGE_FOLDER / name).read_text(encoding=_FILE_ENCODING)
