"""ALLOWED set of the guided-flow clean_ckpt helper."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "contrib/lewm_hillclimb_guided/clean_ckpt.py"


def _load():
    spec = importlib.util.spec_from_file_location("guided_clean_ckpt", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_allowed_covers_every_name_the_flow_cleans():
    allowed = _load().ALLOWED
    # every checkpoint dir the flow asks clean_ckpt to remove must be allowed
    for name in ("lewm_cube_exp", "lewm_smoke", "lewm_cube_confirm", "lewm_cube_paper"):
        assert name in allowed, f"{name} must be cleanable by the flow"


def test_user_checkpoints_still_protected():
    allowed = _load().ALLOWED
    for name in ("lewm", "lewm_cube", "lewm_reacher"):
        assert name not in allowed
