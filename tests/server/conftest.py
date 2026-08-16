"""Shared fixtures for tests/server/ — available to test_jobs, test_api, etc."""
import pytest

SLEEPER = (
    "provider: {type: local, model: m}\n"
    "shared: {seconds: '30'}\n"
    "workflow:\n  - {id: nap, type: command, run: 'sleep {{ seconds }}'}\n"
)


@pytest.fixture
def sleeper_flow(tmp_path):
    """Create a minimal sleeper flow under tmp_path/flows/sleeper/flow.yaml."""
    d = tmp_path / "flows" / "sleeper"
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(SLEEPER)
    return d / "flow.yaml"
