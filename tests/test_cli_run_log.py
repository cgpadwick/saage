"""_attach_run_log must not accumulate FileHandlers across repeated main()/resume
calls in one process (tests/embedding) — otherwise logs duplicate and fds leak."""
import logging

from saage.cli import _attach_run_log


def _run_log_handlers():
    return [h for h in logging.getLogger().handlers
            if getattr(h, "_saage_run_log", False)]


def test_attach_run_log_replaces_prior_handler(tmp_path):
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    d1.mkdir(); d2.mkdir()
    try:
        _attach_run_log(d1)
        _attach_run_log(d2)
        handlers = _run_log_handlers()
        assert len(handlers) == 1                       # second call replaced the first
        assert handlers[0].baseFilename.endswith("r2/run.log")   # points at the latest run
    finally:
        for h in _run_log_handlers():
            logging.getLogger().removeHandler(h)
            h.close()
