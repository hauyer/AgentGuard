"""Deterministic, transparent features for message-level screening baselines."""

from __future__ import annotations

import math
import re
from collections import Counter

_BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/])")
_HEX_RUN = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{48,}(?![0-9a-fA-F])")
_TOKEN = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def extract_message_features(text: str) -> dict[str, str | int | float | bool]:
    """Extract reproducible surface features; these are indicators, not labels."""
    value = text or ""
    counts = Counter(value)
    total_chars = len(value)
    entropy = 0.0
    if total_chars:
        for count in counts.values():
            probability = count / total_chars
            entropy -= probability * math.log2(probability)

    tokens = _TOKEN.findall(value.lower())
    token_count = len(tokens)
    unique_ratio = len(set(tokens)) / token_count if token_count else 0.0
    base64_matches = _BASE64_RUN.findall(value)
    hex_matches = _HEX_RUN.findall(value)
    longest_encoded_run = max(
        [len(item) for item in base64_matches + hex_matches] or [0]
    )
    return {
        "message_length": total_chars,
        "token_count": token_count,
        "unique_token_ratio": round(unique_ratio, 6),
        "character_entropy": round(entropy, 6),
        "base64_like_run": bool(base64_matches),
        "hex_like_run": bool(hex_matches),
        "longest_encoded_run": longest_encoded_run,
    }
