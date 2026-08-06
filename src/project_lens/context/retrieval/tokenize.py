"""Identifier-aware English and Chinese tokenization."""

from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.$:/-]*|\d+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.findall(str(text or "")):
        lowered = match.lower()
        if re.fullmatch(r"[\u4e00-\u9fff]+", lowered):
            tokens.extend(_chinese_ngrams(lowered))
        else:
            tokens.append(lowered)
            tokens.extend(_identifier_parts(lowered))
    return list(dict.fromkeys(token for token in tokens if token))


def _identifier_parts(token: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", token).lower()
    return [part for part in re.split(r"[_.$:/-]+", normalized) if len(part) > 1]


def _chinese_ngrams(text: str) -> list[str]:
    if len(text) <= 2:
        return [text]
    grams = [text]
    grams.extend(text[index : index + 2] for index in range(len(text) - 1))
    grams.extend(text[index : index + 3] for index in range(len(text) - 2))
    return grams

