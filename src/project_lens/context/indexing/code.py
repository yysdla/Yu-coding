"""Python AST indexing that preserves symbols, imports, and source line ranges."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from project_lens.context.indexing.common import content_hash, utc_now
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef


class PythonCodeIndexer:
    _ignored_directories = {".git", ".venv", "venv", "__pycache__", "node_modules"}

    def index(
        self,
        root: Path,
        *,
        project: ProjectRef,
        access_scope: str,
        observed_at: datetime | None = None,
    ) -> list[Evidence]:
        root = root.resolve()
        indexed: list[Evidence] = []
        for path in sorted(root.rglob("*.py")):
            if any(part in self._ignored_directories for part in path.parts):
                continue
            indexed.extend(
                self._index_file(
                    root,
                    path,
                    project=project,
                    access_scope=access_scope,
                    observed_at=observed_at or utc_now(),
                )
            )
        return indexed

    def _index_file(
        self,
        root: Path,
        path: Path,
        *,
        project: ProjectRef,
        access_scope: str,
        observed_at: datetime,
    ) -> list[Evidence]:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative_path = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return [
                self._make_evidence(
                    project=project,
                    relative_path=relative_path,
                    content=text,
                    access_scope=access_scope,
                    observed_at=observed_at,
                    start_line=1,
                    end_line=max(1, len(text.splitlines())),
                    symbol=None,
                    symbol_type="syntax_error_file",
                    imports=(),
                    extra={"syntax_error": str(exc)},
                )
            ]

        imports = tuple(_imports(tree))
        evidence: list[Evidence] = []
        module_doc = ast.get_docstring(tree)
        if module_doc:
            evidence.append(
                self._make_evidence(
                    project=project,
                    relative_path=relative_path,
                    content=module_doc,
                    access_scope=access_scope,
                    observed_at=observed_at,
                    start_line=1,
                    end_line=min(len(text.splitlines()), max(1, module_doc.count("\n") + 2)),
                    symbol=None,
                    symbol_type="module",
                    imports=imports,
                )
            )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            segment = ast.get_source_segment(text, node) or ""
            if not segment.strip():
                continue
            symbol_type = "class" if isinstance(node, ast.ClassDef) else "function"
            route_hint = _route_hint(ast.get_docstring(node) or segment)
            evidence.append(
                self._make_evidence(
                    project=project,
                    relative_path=relative_path,
                    content=segment,
                    access_scope=access_scope,
                    observed_at=observed_at,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    symbol=node.name,
                    symbol_type=symbol_type,
                    imports=imports,
                    extra=route_hint,
                )
            )
        if not evidence and text.strip():
            evidence.append(
                self._make_evidence(
                    project=project,
                    relative_path=relative_path,
                    content=text,
                    access_scope=access_scope,
                    observed_at=observed_at,
                    start_line=1,
                    end_line=max(1, len(text.splitlines())),
                    symbol=None,
                    symbol_type="module",
                    imports=imports,
                )
            )
        return evidence

    @staticmethod
    def _make_evidence(
        *,
        project: ProjectRef,
        relative_path: str,
        content: str,
        access_scope: str,
        observed_at: datetime,
        start_line: int,
        end_line: int,
        symbol: str | None,
        symbol_type: str,
        imports: Iterable[str],
        extra: dict | None = None,
    ) -> Evidence:
        source_id = f"{relative_path}#L{start_line}-L{end_line}"
        metadata = {
            "file": relative_path,
            "start_line": start_line,
            "end_line": end_line,
            "symbol": symbol,
            "symbol_type": symbol_type,
            "imports": tuple(imports),
            **(extra or {}),
        }
        return Evidence(
            type=EvidenceType.CODE,
            project=project,
            source=SourceRef(system="local_repository", source_id=source_id),
            content=content,
            observed_at=observed_at,
            access_scope=access_scope,
            content_hash=content_hash(content),
            metadata=metadata,
        )


def _imports(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _route_hint(text: str) -> dict[str, str]:
    match = re.search(
        r"(?:route|endpoint)\s*:\s*(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?(/[^\s`'\"，。]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return {}
    method = (match.group(1) or "").upper()
    metadata = {"endpoint_path": match.group(2)}
    if method:
        metadata["http_method"] = method
    return metadata
