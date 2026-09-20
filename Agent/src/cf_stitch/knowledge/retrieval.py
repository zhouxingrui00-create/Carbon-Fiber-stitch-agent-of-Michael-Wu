"""Literal, offline text search helpers; evidence is never executable input."""

from __future__ import annotations

import unicodedata


def normalize_search_text(text: str) -> str:
    """Normalize a separate search index without altering stored original text."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def search_terms(query: str) -> tuple[str, ...]:
    """Whitespace-separated terms use AND; punctuation and SQL are literal."""
    if not isinstance(query, str):
        raise ValueError("检索词必须是文本。")
    if len(query) > 500:
        raise ValueError("检索词最多 500 个字符。")
    terms = tuple(dict.fromkeys(normalize_search_text(query).split()))
    if len(terms) > 20:
        raise ValueError("一次最多输入 20 个以空白分隔的检索词。")
    return terms
