"""saage.remote — hand a flow off to a remote SSH-able node and run it there.

The node is the master: the saage engine runs on the node, unchanged. The
local machine packages (code + workspace git ref + secrets), pushes, starts
the run detached under tmux, and disconnects. Artifacts land in the node-side run
directory (``~/.saage_runs/<run_id>/artifacts``), which `status`/`fetch` read
back over SSH. See docs/remote_handoff_plan.md.

Programmatic surface (what a dispatcher/sweep builds on):
``handoff`` to start a run, ``poll_run``/``fetch_run``/``kill_run`` to manage
it, ``find_run``/``RunState`` for local state.
"""
from .handoff import HandoffError, handoff               # noqa: F401
from .observe import fetch_run, kill_run, poll_run       # noqa: F401
from .provision import ProvisionError, provision_node    # noqa: F401
from .state import RunState, find_run, list_runs         # noqa: F401
