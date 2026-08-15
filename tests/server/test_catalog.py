"""Server config and catalog tests."""
from saage.server.config import load_server_config


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
