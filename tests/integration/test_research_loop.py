"""The nested researcher gate, offline: format retries (deterministic
check_ideas + feedback), then the substance critic (scripted), with the
inner loop re-running on outer rejection.

Story: attempt 1 writes structurally bad JSON -> check_ideas fails ->
attempt 2 writes a valid menu -> rendered -> critic REJECTS (semantic
duplicates) -> outer retry resets the inner loop -> attempt 3 writes the
improved menu -> critic passes. The rendered markdown must be attempt 3's.
"""
import json

import yaml
from saage_testkit import RoutedProvider, call, resp

from saage.hydrate import run_flow

RESEARCH_STEP = {
    "id": "research_loop",
    "type": "retry_loop",
    "max_iterations": 5,
    "action": {
        "id": "research_format_loop",
        "type": "retry_loop",
        "max_iterations": 3,
        "action": {"id": "research_ideas", "type": "agent",
                   "skill": "research_ideas", "max_steps": 25},
        "check": {"id": "check_ideas", "type": "command",
                  "run": 'python3 "{{ flow_dir }}/check_ideas.py"'},
    },
    "check": {"id": "research_critic", "type": "agent",
              "skill": "research_critic", "max_steps": 12},
}


def _menu(tag: str) -> str:
    cats = ["feature representation", "model family",
            "optimization & schedule", "regularization",
            "data handling", "ensembling"]
    return json.dumps({
        "constraints": ["budget fixed", "contract frozen"],
        "ideas": [{"rank": i + 1, "title": f"{tag} idea {i + 1}",
                   "category": cats[i], "change": f"{tag} change {i + 1}",
                   "why": "EDA says so", "cost": "fits"} for i in range(6)],
        "anti_ideas": [{"technique": "flips", "reason": "hurts"}],
    })


def test_research_loop_format_then_substance_gates(flow_copy, tmp_path):
    flow_dir = flow_copy("kaggle_solver").parent
    flow_yaml = flow_dir / "research_only.yaml"
    flow_yaml.write_text(yaml.safe_dump({
        "provider": {"type": "local", "model": "x"},
        "shared": {"short_epochs": 15},
        "workflow": [RESEARCH_STEP],
    }))
    ws = tmp_path / "ws"
    ws.mkdir()

    provider = RoutedProvider({
        "research_ideas": [
            # attempt 1: structurally broken (empty ideas list)
            resp(calls=[call("write_file", path="autoresearch_ideas.json",
                             content='{"ideas": []}')]),
            resp("wrote menu v1"),
            # attempt 2 (gets check_ideas ISSUEs as feedback): valid menu
            resp(calls=[call("write_file", path="autoresearch_ideas.json",
                             content=_menu("v2"))]),
            resp("wrote menu v2"),
            # attempt 3 (gets the critic's feedback): improved menu
            resp(calls=[call("write_file", path="autoresearch_ideas.json",
                             content=_menu("v3final"))]),
            resp("wrote menu v3"),
        ],
        "research_critic": [
            resp("ideas 2 and 4 are the same mechanism rephrased — "
                 "replace one with a genuinely different family.\nACTION: fail"),
            resp("concrete, grounded, honestly ranked.\nACTION: pass"),
        ],
    })
    run_flow(flow_yaml, provider=provider, workspace=ws)

    md = (ws / "autoresearch_ideas.md").read_text()
    assert "v3final idea 1" in md          # the post-critic menu is what's live
    assert "v2 idea" not in md
    assert "### 1." in md and "## Anti-ideas" in md
    # every scripted turn was consumed: 3 researcher attempts, 2 critic calls
    assert not provider.queues["research_ideas"]
    assert not provider.queues["research_critic"]
