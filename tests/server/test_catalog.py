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



def test_malformed_yaml_becomes_broken_flow_not_crash(tmp_path):
    """One unparseable flow.yaml must not abort the whole catalog refresh."""
    base = tmp_path / "flows"
    good = base / "good"
    good.mkdir(parents=True)
    good.joinpath("flow.yaml").write_text(
        "provider: {type: local, model: m}\n"
        "workflow:\n  - {id: a, type: command, run: 'echo hi'}\n")
    bad = base / "bad"
    bad.mkdir()
    bad.joinpath("flow.yaml").write_text("{unclosed: [")   # YAML parse error
    listy = base / "listy"
    listy.mkdir()
    listy.joinpath("flow.yaml").write_text("- not\n- a\n- mapping\n")

    cat = FlowCatalog(ServerConfig(flow_paths=[base]))
    cat.refresh()                                          # must not raise

    assert cat.get("good").error is None
    assert cat.get("bad").error is not None
    assert cat.get("listy").error is not None and "mapping" in cat.get("listy").error


class TestConfigSource:
    def test_source_none_when_missing(self, tmp_path):
        assert load_server_config(tmp_path / "nope.yaml").source is None

    def test_source_set_when_read(self, tmp_path):
        p = tmp_path / "server.yaml"
        p.write_text("port: 9000\n")
        assert load_server_config(p).source == p.resolve()
