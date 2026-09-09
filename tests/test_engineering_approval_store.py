from datetime import timedelta
from project_lens.domain.approval import ApprovalKind, ApprovalRecord, ApprovalStatus
from project_lens.domain.models import ProjectRef, utc_now
from project_lens.persistence.sqlite import SQLiteDatabase, SQLiteEngineeringApprovalStore

def test_engineering_approval_store_persists_and_decides() -> None:
    database = SQLiteDatabase(":memory:")
    store = SQLiteEngineeringApprovalStore(database)
    approval = ApprovalRecord(kind=ApprovalKind.ENGINEERING_APPLY, proposal_id=None, project=ProjectRef(tenant_id="demo", project_id="p"), requested_by="hermes", expires_at=utc_now() + timedelta(hours=1))
    store.create(approval)
    assert store.get(approval.approval_id).decision is ApprovalStatus.PENDING
    decided = store.decide(approval.approval_id, approved=True, decided_by="manager")
    assert decided is not None and decided.decision is ApprovalStatus.APPROVED

def test_engineering_approval_store_rejects_expired_decision() -> None:
    database = SQLiteDatabase(":memory:")
    store = SQLiteEngineeringApprovalStore(database)
    approval = ApprovalRecord(kind=ApprovalKind.ENGINEERING_APPLY, project=ProjectRef(tenant_id="demo", project_id="p"), requested_by="hermes", expires_at=utc_now() - timedelta(minutes=1))
    store.create(approval)
    try:
        store.decide(approval.approval_id, approved=True, decided_by="manager")
    except ValueError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("expired approval was decided")
    assert store.get(approval.approval_id).decision is ApprovalStatus.EXPIRED
