"""Every flow under flows/ must hydrate (offline schema + skill-wiring check).

`build_flow` parses flow.yaml, loads the skill.md files, validates step types,
and wires the loop graph — without running any command or importing helper
scripts. So this is a cheap, network-free guard that catches a broken flow.yaml,
a renamed/missing skill, or a bad loop wiring in CI, for heavy application flows
(greenfield_ml, kaggle_solver) that have no end-to-end test.
"""
from pathlib import Path

import pytest

from saage.hydrate import build_flow

FLOWS = Path(__file__).resolve().parent.parent / "flows"
_FLOW_YAMLS = sorted(p for p in FLOWS.iterdir() if (p / "flow.yaml").is_file())


@pytest.mark.parametrize("flow_dir", _FLOW_YAMLS, ids=lambda p: p.name)
def test_flow_hydrates(flow_dir, tmp_path):
    # provider=object() skips real provider construction; tmp workspace avoids
    # touching the flow dir.
    build_flow(flow_dir / "flow.yaml", provider=object(), workspace=str(tmp_path))
