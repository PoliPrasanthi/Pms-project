
from __future__ import annotations

import re


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(value: str | None) -> str | None:
    
    if value is None:
        return None
    # Remove every HTML tag
    text = _HTML_TAG_RE.sub(" ", value)
    # Decode common HTML entities
    text = (
        text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&nbsp;", " ")
    )
    # Collapse whitespace and strip leading/trailing
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text
