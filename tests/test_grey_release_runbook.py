"""Ensure the Feishu grey-release runbook stays discoverable and complete."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "feishu-grey-release.md"


def test_feishu_grey_release_runbook_has_required_sections() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "只读" in text
    assert "介绍一下这个项目" in text
    assert "项目地图" in text
    assert "给产品看的版本" in text
    assert "展开技术细节" in text
    assert "PROJECT_LENS_MODEL_PROVIDER=stub" in text
    assert "allow_apply" in text
    assert "紧急开关" in text or "回滚" in text
    assert "test_stub_pilot_smoke.py" in text
    assert "test_safe_live_pilot_smoke.py" in text
