"""Flow C — command capture (job_id) feeding a synchronous polling_loop."""
from saage_testkit import RoutedProvider, resp

from saage.hydrate import run_flow


def test_poll_job(flow_copy):
    flow_yaml = flow_copy("poll_job")
    # scheduler reports RUNNING twice, then COMPLETE
    provider = RoutedProvider({
        "classify": [resp("ACTION: running"),
                     resp("ACTION: running"),
                     resp("ACTION: complete")],
    })
    shared = run_flow(flow_yaml, provider=provider)

    assert shared["job_id"] == 4242                  # captured from submit.py output
    assert shared["_trace"].count("poll") == 3       # polled 3x then stopped
    assert shared["_trace"].count("classify") == 3
    assert shared["_trace"][-1] == "classify"        # terminated on COMPLETE, not a hang
