"""Home-page empty state + serve() startup behavior (flow discovery, logging)."""
import pytest

pytest.importorskip("fastapi", reason="server extra not installed")

from fastapi.testclient import TestClient  # noqa: E402

from saage.server.app import create_app, serve  # noqa: E402
from saage.server.config import ServerConfig  # noqa: E402

GOOD = ("# A demo flow.\n"
        "provider: {type: local, model: m}\n"
        "shared: {seconds: '1'}\n"
        "workflow:\n  - {id: s1, type: command, run: 'sleep {{ seconds }}'}\n")


def _write_flow(root, name="demo"):
    d = root / "flows" / name
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(GOOD)


def test_home_shows_empty_state_with_guidance(tmp_path):
    c = TestClient(create_app(ServerConfig(flow_paths=[tmp_path / "flows"])))
    body = c.get("/").text
    assert "No flows found" in body
    assert "--flow-path" in body
    assert str(tmp_path / "flows") in body       # says where it looked


def test_home_empty_state_gone_when_flows_exist(tmp_path):
    _write_flow(tmp_path)
    c = TestClient(create_app(ServerConfig(flow_paths=[tmp_path / "flows"])))
    body = c.get("/").text
    assert "No flows found" not in body
    assert "demo" in body


def test_home_flags_disabled_nl_launcher(tmp_path):
    c = TestClient(create_app(ServerConfig(flow_paths=[tmp_path / "flows"])))
    assert "parser_provider" in c.get("/").text  # disabled hint names the fix

    cfg = ServerConfig(flow_paths=[tmp_path / "flows"],
                       parser_provider={"type": "local", "model": "m"})
    assert "Disabled" not in TestClient(create_app(cfg)).get("/").text


def test_serve_autodiscovers_cwd_flows(tmp_path, monkeypatch):
    """No config + a flows/ dir where the server starts -> flows just show up."""
    _write_flow(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    assert serve(config_path=str(tmp_path / "nope.yaml")) == 0
    assert "demo" in captured["app"].state.catalog.flows


def test_serve_flow_path_flag_overrides(tmp_path, monkeypatch):
    _write_flow(tmp_path, "flagged")
    captured = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    serve(config_path=str(tmp_path / "nope.yaml"),
          flow_paths=[str(tmp_path / "flows")])
    assert "flagged" in captured["app"].state.catalog.flows
