import json
from pathlib import Path

import pytest

from project_lens.context.bootstrap import (
    LocalContextSources,
    LocalProjectRegistration,
    build_registered_context_engine,
)
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.identity import parse_project_bindings


def test_second_project_can_be_indexed_and_retrieved_in_isolation(tmp_path: Path) -> None:
    payment_root = tmp_path / "payment"
    inventory_root = tmp_path / "inventory"
    payment_root.mkdir()
    inventory_root.mkdir()
    (payment_root / "payment_service.py").write_text(
        "def charge_invoice():\n    return 'payment-only'\n", encoding="utf-8"
    )
    (inventory_root / "inventory_service.py").write_text(
        "def reserve_stock():\n    return 'inventory-only'\n", encoding="utf-8"
    )
    payment = ProjectRef(tenant_id="acme", project_id="payment", service="billing")
    inventory = ProjectRef(tenant_id="acme", project_id="inventory", service="warehouse")
    engine, _ = build_registered_context_engine(
        (
            LocalProjectRegistration(
                project=payment,
                sources=LocalContextSources(repository_root=payment_root),
                access_scope="project:payment:read",
            ),
            LocalProjectRegistration(
                project=inventory,
                sources=LocalContextSources(repository_root=inventory_root),
                access_scope="project:inventory:read",
            ),
        )
    )

    bundle = engine.search(
        ContextQuery(text="reserve stock", project=inventory),
        AccessContext(
            tenant_id="acme",
            user_id="u1",
            permissions=frozenset({"project:inventory:read"}),
        ),
    )

    assert bundle.evidence
    assert all(item.project == inventory for item in bundle.evidence)
    assert all("payment-only" not in item.content for item in bundle.evidence)


def test_feishu_binding_resolves_registered_second_project() -> None:
    payment = ProjectRef(tenant_id="acme", project_id="payment", service="billing")
    inventory = ProjectRef(tenant_id="acme", project_id="inventory", service="warehouse")
    mapper = parse_project_bindings(
        json.dumps(
            {
                "bindings": [
                    {
                        "tenant_key": "feishu-acme",
                        "chat_id": "oc_inventory",
                        "project_id": "inventory",
                        "allowed_users": ["ou_123"],
                    }
                ]
            }
        ),
        default_project=payment,
        registered_projects=(payment, inventory),
    )

    context = mapper.resolve(
        tenant_key="feishu-acme", chat_id="oc_inventory", user_id="ou_123"
    )

    assert context.project == inventory
    with pytest.raises(PermissionError, match="not allowed"):
        mapper.resolve(tenant_key="feishu-acme", chat_id="oc_inventory", user_id="ou_other")


def test_feishu_binding_rejects_an_unregistered_project() -> None:
    payment = ProjectRef(tenant_id="acme", project_id="payment", service="billing")

    with pytest.raises(ValueError, match="unregistered"):
        parse_project_bindings(
            '{"bindings":[{"tenant_key":"feishu-acme","chat_id":"oc_unknown",'
            '"project_id":"unknown"}]}',
            default_project=payment,
            registered_projects=(payment,),
        )


def test_feishu_binding_accepts_project_space_from_config_projects() -> None:
    """ProjectSpace loaded from config/projects is the Feishu binding source of truth."""

    from project_lens.main import create_app

    app = create_app()
    registry = app.state.project_registry
    crm = registry.get("demo", "crm")
    assert crm is not None
    registered = tuple(space.project for space in registry.list())
    assert any(item.project_id == "crm" for item in registered)

    mapper = parse_project_bindings(
        json.dumps(
            {
                "bindings": [
                    {
                        "tenant_key": "feishu-demo",
                        "chat_id": "oc_crm",
                        "lens_tenant_id": "demo",
                        "project_id": "crm",
                    }
                ]
            }
        ),
        default_project=registry.require("demo", "payment").project,
        registered_projects=registered,
    )

    context = mapper.resolve(
        tenant_key="feishu-demo",
        chat_id="oc_crm",
        user_id="u1",
    )
    assert context.project.tenant_id == "demo"
    assert context.project.project_id == "crm"
    assert context.project.service == "contact-service"


def test_feishu_binding_rejects_project_absent_from_project_registry() -> None:
    from project_lens.main import create_app

    app = create_app()
    registered = tuple(space.project for space in app.state.project_registry.list())
    payment = app.state.project_registry.require("demo", "payment").project

    with pytest.raises(ValueError, match="unregistered"):
        parse_project_bindings(
            json.dumps(
                {
                    "bindings": [
                        {
                            "tenant_key": "feishu-demo",
                            "chat_id": "oc_ghost",
                            "lens_tenant_id": "demo",
                            "project_id": "does-not-exist",
                        }
                    ]
                }
            ),
            default_project=payment,
            registered_projects=registered,
        )
