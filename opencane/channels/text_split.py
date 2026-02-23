"""Utility helpers for splitting outbound text by platform limits."""

from __future__ import annotations


def split_message(content: str, max_len: int) -> list[str]:
    """Split content into chunks <= max_len, preferring newline then space boundaries."""
    text = str(content or "")
    limit = max(1, int(max_len))
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        if text[limit: limit + 1].isspace():
            pos = limit
        else:
            head = text[:limit]
            pos = head.rfind("\n")
            if pos <= 0:
                pos = head.rfind(" ")
            if pos <= 0:
                pos = limit

        chunks.append(text[:pos])
        text = text[pos:].lstrip()

    return chunks
