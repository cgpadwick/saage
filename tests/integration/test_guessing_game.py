"""Flow D — multi-agent feedback loop: guess a hidden number via higher/lower.

The guesser is never told the target; the judge replies higher/lower/correct.
The loop exits (via exit_when) when the judge says "correct". Both agents are
scripted here; the `record` command and the shared history.txt are real.
"""
from saage_testkit import RoutedProvider, resp

from saage.hydrate import run_flow


def test_guessing_game(flow_copy):
    flow_yaml = flow_copy("guessing_game")
    # guesser homes in: 0.5 (too low) -> 0.75 (too high) -> 0.62 (correct)
    provider = RoutedProvider({
        "propose": [resp("0.5"), resp("0.75"), resp("0.62")],
        "judge":   [resp("higher"), resp("lower"), resp("correct")],
    })
    shared = run_flow(flow_yaml, provider=provider)

    assert shared["_trace"] == ["propose", "judge", "record"] * 3
    assert shared["_iter"]["search"] == 3
    assert shared["_exit_reason"]["search"] == "exit_when"   # solved, not max-iters
    assert shared["feedback"] == "correct"
    assert shared["guess"] == 0.62

    # the shared scratchpad accumulated one line per round
    history = (flow_yaml.parent / "history.txt").read_text().strip().splitlines()
    assert history == ["guess=0.5 -> higher", "guess=0.75 -> lower",
                       "guess=0.62 -> correct"]
