"""Text-cleaning helpers for the news domain -- mirrors engine/src/news.rs."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

_NBSP = " "


def normalize_for_match(value: str) -> str:
    out_chars: list[str] = []
    for ch in value:
        if ch == "&":
            out_chars.append(" AND ")
        elif ch.isascii() and ch.isalnum():
            out_chars.append(ch.upper())
        else:
            out_chars.append(" ")
    return " ".join("".join(out_chars).split())


def clean_text(raw: str) -> str:
    text = BeautifulSoup(raw, "lxml").get_text(" ")
    if not text.strip():
        text = raw
    text = (
        text.replace(_NBSP, " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#039;", "'")
        .replace("&#8217;", "'")
    )
    return " ".join(text.split())


def truncate_chars(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else value[:max_chars]


def canonical_url(url: str) -> str:
    return url.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")


def resolve_url(base: str, href: str) -> str | None:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    try:
        return urljoin(base, href)
    except ValueError:
        return None
