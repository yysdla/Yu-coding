"""Small deterministic YAML frontmatter codec for generated Markdown."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from project_lens.obsidian.errors import FrontmatterError


def serialize_frontmatter(metadata: Mapping[str, object]) -> str:
    if not metadata:
        raise FrontmatterError("frontmatter cannot be empty")
    lines = ["---"]
    for key in sorted(metadata):
        if not key or "\n" in key or ":" in key:
            raise FrontmatterError(f"invalid frontmatter key: {key!r}")
        value = metadata[key]
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def parse_frontmatter(document: str) -> tuple[dict[str, Any], str]:
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("document must start with frontmatter delimiter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise FrontmatterError("frontmatter closing delimiter is missing") from exc
    metadata: dict[str, Any] = {}
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith(" "):
            raise FrontmatterError("unexpected indentation in frontmatter")
        if ":" not in line:
            raise FrontmatterError(f"invalid frontmatter line: {line!r}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key or key in metadata:
            raise FrontmatterError(f"duplicate or empty frontmatter key: {key!r}")
        raw_value = raw_value.strip()
        if raw_value:
            metadata[key] = _parse_scalar(raw_value)
            index += 1
            continue
        values: list[Any] = []
        index += 1
        while index < end and lines[index].startswith("  - "):
            values.append(_parse_scalar(lines[index][4:].strip()))
            index += 1
        metadata[key] = values
    # A blank line after frontmatter is a Markdown separator, not page content.
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return metadata, body


def _yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise FrontmatterError(f"unsupported frontmatter value: {type(value).__name__}")


def _parse_scalar(value: str) -> object:
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    if value.startswith('\"') and value.endswith('\"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise FrontmatterError("invalid quoted frontmatter scalar") from exc
    try:
        return int(value)
    except ValueError:
        return value
