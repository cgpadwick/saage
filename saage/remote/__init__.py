"""saage.remote — hand a flow off to a remote SSH-able node and run it there.

The node is the master: the saage engine runs on the node, unchanged. The
local machine packages (code + workspace git ref + secrets), pushes, starts
the run detached under tmux, and disconnects. Artifacts land in the node-side run
directory (``~/.saage_runs/<run_id>/artifacts``), which `status`/`fetch` read
back over SSH. See docs/remote_handoff_plan.md.
"""
