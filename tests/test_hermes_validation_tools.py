from project_lens.application.hermes_validation import check_diff_scope, verify_evidence_links
from project_lens.runtime.patch_plan import FilePatch, PatchPlan

def test_check_diff_scope_rejects_paths_outside_src_and_tests() -> None:
    result = check_diff_scope(PatchPlan("scope", "check", (FilePatch("docs/readme.md", "a", "b"),)))
    assert result.ok is False
    assert result.errors

def test_verify_evidence_links_reports_unknown_citations() -> None:
    result = verify_evidence_links(evidence_ids=("e1",), citation_ids=("e1", "e2"))
    assert result.ok is False
    assert "e2" in result.errors[0]
