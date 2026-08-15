"""Server config and flow catalog tests."""
from saage.server.catalog import FlowCatalog
from saage.server.config import ServerConfig, load_server_config


class TestServerConfig:
    def test_missing_file_yields_defaults(self, tmp_path):
        cfg = load_server_config(tmp_path / "nope.yaml")
        assert cfg.flow_paths == [] and cfg.port == 8321 and cfg.host == "127.0.0.1"

    def test_loads_and_expands_paths(self, tmp_path):
        (tmp_path / "server.yaml").write_text(
            "flow_paths: ['~/flows_a', 'rel_b']\n"
            "port: 9000\n"
            "parser_provider: {type: local, model: m}\n")
        cfg = load_server_config(tmp_path / "server.yaml")
        assert cfg.flow_paths[0].is_absolute()          # ~ expanded
        assert cfg.port == 9000
        assert cfg.parser_provider == {"type": "local", "model": "m"}

    def test_malformed_port_degrades_gracefully(self, tmp_path):
        (tmp_path / "server.yaml").write_text("port: notanumber\n")
        cfg = load_server_config(tmp_path / "server.yaml")
        assert cfg.port == 8321


GOOD = ("# A demo flow.\n# Second line ignored.\n"
        "provider: {type: local, model: m}\n"
        "shared: {knob_a: '1', knob_b: hello}\n"
        "workflow:\n  - {id: s1, type: command, run: 'echo hi'}\n")
BROKEN = "provider: {type: local, model: m}\nworkflow:\n  - {id: s1, type: nope}\n"


def _mk(tmp_path, name, text):
    d = tmp_path / "flows" / name
    d.mkdir(parents=True)
    (d / "flow.yaml").write_text(text)


class TestCatalog:
    def test_discovers_and_extracts_knobs(self, tmp_path):
        _mk(tmp_path, "demo", GOOD)
        cat = FlowCatalog(ServerConfig(flow_paths=[tmp_path / "flows"]))
        cat.refresh()
        info = cat.get("demo")
        assert info.error is None
        assert info.knobs == {"knob_a": "1", "knob_b": "hello"}
        assert info.description == "A demo flow."

    def test_broken_flow_listed_with_error(self, tmp_path):
        _mk(tmp_path, "bad", BROKEN)
        cat = FlowCatalog(ServerConfig(flow_paths=[tmp_path / "flows"]))
        cat.refresh()
        assert cat.get("bad").error          # listed, not hidden

