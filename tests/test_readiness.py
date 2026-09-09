from project_lens.api.routes import ready


class _State:
    def __init__(self, *, bridge: object | None = object()) -> None:
        self.context_engine = object()
        self.run_service = object()
        self.project_registry = object()
        self.hermes_runtime_service = object()
        self.feishu_hermes_tool_loop_bridge = bridge
        self.hermes_tool_loop_enabled = True
        self.risk_engine = object()
        self.database = object()


class _App:
    def __init__(self, *, bridge: object | None = object()) -> None:
        self.state = _State(bridge=bridge)


class _Request:
    def __init__(self, *, bridge: object | None = object()) -> None:
        self.app = _App(bridge=bridge)


def test_readiness_reports_ready_when_core_services_exist() -> None:
    result = ready(_Request())
    assert result.ready is True
    assert result.status == "ready"
    assert all(result.checks.values())


def test_readiness_reports_not_ready_when_enabled_hermes_bridge_missing() -> None:
    result = ready(_Request(bridge=None))
    assert result.ready is False
    assert result.checks["hermes_bridge"] is False
