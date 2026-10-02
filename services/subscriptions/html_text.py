"""Email → plain text for the model: keep what a person reads, drop what they don't.

Marketing emails are mostly layout: hidden "preheader" text, tracking links, spacer images and
nested tables. The model only needs the words and the numbers, in reading order, so this keeps
text, puts table cells on one line ("Amount | $15.49"), and drops scripts, styles, hidden
elements, images, and URLs. Uses only the standard library and never raises on broken HTML.
"""

import re
from html.parser import HTMLParser

MAX_CHARS = 20_000  # ~5k tokens: enough for any receipt, a cap for marketing noise

_SKIP = frozenset({"script", "style", "head", "title", "noscript", "template", "svg"})
_VOID = frozenset({"br", "hr", "img", "meta", "link", "input", "wbr", "source", "area", "col"})
_PARAGRAPH = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote"})
_LINE = frozenset({"div", "tr", "li", "section", "article", "header", "footer", "dt", "dd"})
_CELL = frozenset({"td", "th"})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_URL = re.compile(r"<?https?://\S+>?")
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_SPACES = dict.fromkeys(map(ord, "   \t\r\f\v"), " ")


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[str, bool]] = []  # (tag, hides its content)
        self.cells_in_row = 0

    @property
    def hidden(self) -> bool:
        return any(hides for _, hides in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        hides = (
            tag in _SKIP
            or "hidden" in values
            or values.get("aria-hidden") == "true"
            or bool(_HIDDEN_STYLE.search(values.get("style") or ""))
        )
        if tag not in _VOID:
            self.stack.append((tag, hides))
        if self.hidden or hides:
            return
        if tag == "br":
            self.out.append("\n")
        elif tag in _PARAGRAPH or tag == "hr":
            self.out.append("\n\n")
        elif tag in _LINE:
            self.out.append("\n")
        if tag == "tr":
            self.cells_in_row = 0
        elif tag in _CELL:
            if self.cells_in_row:
                self.out.append(" | ")
            self.cells_in_row += 1

    def handle_endtag(self, tag: str) -> None:
        # Close up to the matching open tag; ignore stray end tags (broken HTML).
        if not any(open_tag == tag for open_tag, _ in self.stack):
            return
        while self.stack:
            open_tag, _ = self.stack.pop()
            if open_tag == tag:
                break
        if self.hidden:
            return
        if tag in _PARAGRAPH:
            self.out.append("\n\n")
        elif tag in _LINE:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.out.append(data)


def html_to_text(html: str) -> str:
    parser = _Extractor()  # html.parser is lenient: malformed markup doesn't raise
    parser.feed(html or "")
    parser.close()
    return tidy_text("".join(parser.out))


def tidy_text(text: str) -> str:
    """Plain-text bodies get the same treatment: no URLs, tidy whitespace, a length budget."""
    text = _URL.sub("", (text or "").translate(_INVISIBLE).translate(_SPACES))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    # Drop separator-only lines left by empty table cells, then cap blank runs at one.
    lines = ["" if not line.strip(" |") else line.strip(" |").strip() for line in lines]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0]
    return text
