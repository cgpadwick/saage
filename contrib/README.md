# contrib — applications built with saage

Flows here are **applications built on the engine**, not part of the core. They
solve a specific problem rather than teach a primitive, and they carry weight the
core does not: external repos, datasets, GPUs, or domain setup.

How `contrib/` differs from [`flows/`](../flows):

- `flows/` = the engine's canonical demos (one per primitive) that double as the
  offline integration-test suite. Generic, dependency-free, CI-gated end-to-end.
- `contrib/` = real applications. Lower bar: **not** run end-to-end in CI (they
  need the external world), may require credentials/GPU/an external repo, and are
  not part of the engine's API/stability surface. Each is still **hydrate-checked**
  (`tests/test_flows_hydrate.py` builds every flow here, so a broken `flow.yaml` or
  skill wiring fails CI), and its deterministic helpers may carry unit tests.

Run one exactly like a core flow: `saage run contrib/<name>/flow.yaml`.

## Flows

| flow | what | specifics |
|---|---|---|
| `lewm_hillclimb` | brownfield auto-research on the le-wm repo: tune LeWorldModel training on OGBench-Cube toward the paper's 74% | needs the external `le-wm` repo as its workspace + a GPU box; `cloud_setup.sh` provisions the stack |
| `lewm_hillclimb_guided` | same target, with research-guided proposals (beat the paper, ~76–78%) | same external repo + GPU; adds a curated idea bank |

Both are tied to one external repo + research target — the reason they live in
`contrib/` rather than `flows/`.
