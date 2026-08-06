from uuid import uuid4

from project_lens.domain.models import ProjectRef
from project_lens.runtime.security import PermissionPolicy, RegexSanitizer, RunContext
from project_lens.runtime.types import ToolCategory, ToolDefinition


def make_context(*, permissions: set[str], approvals: set[str] | None = None) -> RunContext:
    return RunContext(
        run_id=uuid4(),
        trace_id=uuid4(),
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        user_id="u1",
        permissions=frozenset(permissions),
        approvals=frozenset(approvals or set()),
    )


def make_definition(category: ToolCategory) -> ToolDefinition:
    return ToolDefinition(
        name="create_task" if category == ToolCategory.WRITE else "read_repo",
        description="test",
        parameters={"type": "object"},
        category=category,
        required_permission="repository:read" if category == ToolCategory.READ else "task:write",
    )


def test_permission_requires_project_scope() -> None:
    decision = PermissionPolicy().check(
        make_definition(ToolCategory.READ),
        make_context(permissions={"repository:read"}),
    )

    assert not decision.allowed
    assert "project:payment:read" in decision.reason


def test_write_tool_requires_explicit_approval() -> None:
    context = make_context(permissions={"project:payment:read", "task:write"})
    decision = PermissionPolicy().check(make_definition(ToolCategory.WRITE), context)

    assert not decision.allowed
    assert "tool:create_task:approve" in decision.reason


def test_sanitizer_redacts_common_secrets() -> None:
    content = "token=abc123456789 password:super-secret Bearer eyJhbGciOiJIUzI1NiJ9.payload"

    sanitized = RegexSanitizer().sanitize(content)

    assert "abc123456789" not in sanitized
    assert "super-secret" not in sanitized
    assert "eyJhbGci" not in sanitized

