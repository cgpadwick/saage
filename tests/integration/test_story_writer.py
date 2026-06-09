"""Flow A — counting_loop with a multi-node body, then a terminal review."""
from saage_testkit import RoutedProvider, call, resp, tool_turn

from saage.hydrate import run_flow


def _scripts():
    return {
        # 3 iterations, each appends one line to story.md
        "scene": tool_turn("run_command", command="printf 'SCENE\\n' >> story.md") * 3,
        "twist": tool_turn("run_command", command="printf 'TWIST\\n' >> story.md") * 3,
        # critic writes review.md then signs off
        "review": [
            resp(calls=[call("run_command", command="printf 'REVIEW\\n' > review.md")]),
            resp("Solid arc. ACTION: complete"),
        ],
    }


def test_story_writer(flow_copy):
    flow_yaml = flow_copy("story_writer")
    shared = run_flow(flow_yaml, provider=RoutedProvider(_scripts()))

    # the loop body alternates scene/twist 3x, then the critic runs once
    assert shared["_trace"] == [
        "scene", "twist", "scene", "twist", "scene", "twist", "critique"]
    assert shared["_iter"]["draft"] == 3

    story = (flow_yaml.parent / "story.md").read_text().split()
    assert story == ["SCENE", "TWIST"] * 3
    assert (flow_yaml.parent / "review.md").exists()


def test_story_writer_is_deterministic(flow_copy):
    # same scripted inputs -> identical trace, twice over (control flow is code)
    traces = []
    for _ in range(2):
        flow_yaml = flow_copy("story_writer")
        shared = run_flow(flow_yaml, provider=RoutedProvider(_scripts()))
        traces.append(shared["_trace"])
    assert traces[0] == traces[1]
