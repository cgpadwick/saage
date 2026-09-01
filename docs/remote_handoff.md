# Remote handoff (`saage remote`)

Develop a flow locally, then hand the *entire run* off to a remote GPU box —
the node runs the unchanged engine under tmux; your machine packages, pushes,
starts, and disconnects. Any flow works remotely with zero flow edits.

```bash
saage remote init                                   # one-time: ssh key + credentials file
saage remote add-target spark --host spark.local --user saage   # any SSH-able box
saage remote handoff flows/greenfield_ml/flow.yaml --target spark \
    --set train_epochs=8                            # package, push, start, disconnect

saage remote status            # phase, heartbeat, ledger, log tail (latest run)
saage remote logs --live       # follow the engine log
saage remote ps                # every target: sessions vs local state (orphan detector)
saage remote fetch             # pull artifacts back: ./results/<run_id>/
saage remote kill <run>        # stop the run — never the box

saage remote list              # registered targets (local, no network)
saage remote cleanup           # prune stale targets: y/N prompt per target
                               #   (--check to ssh-probe first, info only;
                               #   removal only forgets the ssh entry — it
                               #   never terminates a box)
```

A killed remote run is resumable. The engine checkpoint (and any file listed in
the flow's `artifacts:`, e.g. the best model) is mirrored to R2 each sync
(changed-only — big files upload only when they change). Then:

```bash
saage remote resume <run>                 # node still up: resume in place
saage remote resume <run> --target spark  # node gone: fresh box, from the R2 checkpoint
```

Cross-box resume restores the checkpoint + mirrored artifacts from R2 and
reconstructs code from the run branch; heavy regenerable inputs (datasets) are
re-staged by the flow's `cloud_setup`, and the hill-climb continues from its
recorded `best_score`/iteration. To keep the trained best model across a box
death, list its (workspace-relative) path in the flow's `artifacts:`.

Targets are just SSH hosts (a LAN box, a hand-launched cloud instance —
`--port` and `--key` cover NAT'd ports and per-instance keys, e.g. Thunder
Compute). For Lambda Cloud there's provisioning built in:

```bash
saage remote spawn --gpu a100        # launch + register as a target (live capacity/pricing)
saage remote terminate <target>      # stops the meter (the only thing that does, on Lambda)
                                     #   and unregisters the target
```

How it works, briefly:

- **Workspace packaging — a git ref, not files.** Brownfield flows (whose
  `workspace:` is an existing repo) get a `saage-run-<id>` branch: pushed to
  `origin` when possible, `git bundle` fallback otherwise. Uncommitted
  changes: `--dirty abort` (default) / `commit` (snapshot them, your checkout
  untouched) / `ship-head` (package HEAD; for workspaces under active use).
- **Per-run secrets** (LLM key for the flow's provider, repo token) travel
  over ssh stdin into a 0600 `run_env` that is deleted when the run stops.
- **Artifacts**: a sidecar collects ledgers/reports into the node's run dir
  (`~/.saage_runs/<id>/artifacts/`); with a `[storage]` section in
  `~/.saage/credentials.toml` they also mirror to R2/S3, and `status`/`fetch`
  fall back to the mirror when the node is gone. A watchdog stops wedged runs.
- **Flow env setup**: `--ws-setup "bash ../flow/cloud_setup.sh"` runs a
  flow-supplied script inside the workspace at bootstrap (see
  `contrib/lewm_hillclimb/cloud_setup.sh` — curated torch stacks via
  [ml-frameworks](https://github.com/cgpadwick/ml-frameworks) with
  driver-aware CUDA selection, dataset staging from HF, headless-EGL libs).

Design + field notes: [`remote_handoff_plan.md`](remote_handoff_plan.md).

