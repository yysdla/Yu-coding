"""Validation-only checks for Hermes proposals."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from project_lens.runtime.patch_plan import PatchPlan

_ALLOWED_PREFIXES = ("src/", "tests/")
_BLOCKED_COMMAND_MARKERS = ("deploy", "rollback", "restart", "git push", "rm ", "del ", "format ")


@dataclass(frozen=True)
class HermesValidationResult:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    test_commands: tuple[str, ...] = ()


def check_diff_scope(plan: PatchPlan, *, allowed_prefixes: tuple[str, ...] = _ALLOWED_PREFIXES) -> HermesValidationResult:
    """Validate only path scope; never inspect or mutate the worktree."""
    errors: list[str] = []
    paths = tuple(item.path.replace("\\", "/") for item in plan.patches)
    for path in paths:
        normalized = str(PurePosixPath(path))
        if normalized.startswith("../") or normalized.startswith("/") or not normalized.startswith(allowed_prefixes):
            errors.append(f"path outside allowlist: {path}")
    return HermesValidationResult(ok=not errors, errors=tuple(errors), affected_paths=paths, test_commands=tuple(plan.test_commands))


def verify_evidence_links(*, evidence_ids: tuple[str, ...], citation_ids: tuple[str, ...]) -> HermesValidationResult:
    """Check that every cited evidence id is present in the supplied ledger."""
    known = {item.strip() for item in evidence_ids if item.strip()}
    cited = tuple(item.strip() for item in citation_ids if item.strip())
    errors = tuple(f"unknown evidence citation: {item}" for item in cited if item not in known)
    return HermesValidationResult(ok=not errors and bool(cited), errors=errors, warnings=("link validation does not load source bodies",))


def validate_patch_plan(plan: PatchPlan) -> HermesValidationResult:
    errors: list[str] = []
    paths = tuple(item.path.replace("\\", "/") for item in plan.patches)
    for path in paths:
        normalized = str(PurePosixPath(path))
        if normalized.startswith("../") or normalized.startswith("/"):
            errors.append(f"path outside project root: {path}")
        elif not normalized.startswith(_ALLOWED_PREFIXES):
            errors.append(f"path is outside src/tests allowlist: {path}")
    commands = tuple(command.strip() for command in plan.test_commands if command.strip())
    for command in commands:
        lowered = command.casefold()
        if any(marker in lowered for marker in _BLOCKED_COMMAND_MARKERS):
            errors.append(f"test command is not allowlisted: {command}")
    if not plan.patches:
        return HermesValidationResult(
            ok=False,
            errors=tuple(errors) + ("patch plan contains no file patches",),
            affected_paths=paths,
            test_commands=commands,
        )
    return HermesValidationResult(
        ok=not errors,
        errors=tuple(errors),
        warnings=("validation does not execute tests or apply patches",),
        affected_paths=paths,
        test_commands=commands,
    )
