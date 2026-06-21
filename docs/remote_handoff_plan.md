# Remote Handoff for saage Flows

**Status:** v1 implemented (`saage/remote/`, tests in `tests/remote/`) — see
"Implementation status" at the bottom for deltas from this plan.
**Date:** 2026-06-09
**Supersedes:** `docs/cloud_one_button_plan.md` (earlier draft; "cloud" framing)

**Use case:** develop and smoke-test a flow locally, then
`saage remote handoff contrib/lewm_hillclimb/flow.yaml --target spark` — the
*entire run* (engine + training) moves to a remote GPU node and runs to
completion unattended. Close the laptop. Easy button.

**The reframe:** there is no "cloud mode" — everything is a **remote node
handoff**. A DGX Spark on the LAN, a manually-launched Lambda instance, a
Thunder box, an EC2 VM: all of them are just *SSH-able hosts with a GPU*.

**v1 scope:** the user brings a **running node they have SSH access to**.
saage does not provision anything. Auto spin-up of Lambda/Thunder nodes is
designed for (§9) but explicitly descoped — and note that descoping
provisioning does **not** descope cloud: launch a Lambda box by hand in their
dashboard, register it as a target, and handoff works identically.

**Core properties:**

- The remote node is the **master**: the saage engine runs on the node,
  unchanged. Any flow works remotely with **zero engine changes and zero flow
  edits** (the existing `--workspace` flag covers the one path override).
- True fire-and-forget — no laptop uptime requirement.
- The laptop's job: package → push → start → disconnect, plus
  observe/kill/fetch later.
- Trade-off accepted: a (spend-capped, per-run) LLM key goes to the node, and
  results sync *out* continuously. Both handled below.

---

## 1. Architecture

```
┌────────── laptop (launcher/viewer only) ──────────┐
│ saage remote handoff / status / ps / kill / fetch │
│ ~/.saage/ creds + run state (intent)              │
└───────────────┬───────────────────────────────────┘
                │ ssh: push manifest + secrets, start run, disconnect
                ▼
┌─────────── remote node (MASTER) ──────────────────┐
│ saage engine: agents, loops, shared store         │
│ workspace clone (le-wm @ base_sha, run branch)    │
│ dataset pulled from bucket → $STABLEWM_HOME       │
│ per-run LLM key + R2 token + repo PAT (0600 file) │
│ sync sidecar (ledgers/logs/ckpts → R2, 5 min)     │
│ watchdog: stop wedged runs                        │
└───┬───────────────────────────┬───────────────────┘
    │ git push run branch       │ aws s3 sync
    ▼                           ▼
 GitHub (le-wm)            s3://saage-runs/runs/<run_id>/   (Cloudflare R2)
 = code/commits channel    = ledgers/logs/checkpoints channel
```

**Two-channel artifact model** (both load-bearing — see §3.4):

| Channel | Carries |
|---|---|
| git run branch (`saage-hillclimb-<run_id>`) | code state, kept experiments, report commit |
| R2 bucket (`runs/<run_id>/`) | `experiments.jsonl`, `research_log.md`, logs, checkpoints — all deliberately gitignored |

**Bucket host: Cloudflare R2** (decided 2026-06-09). GPU providers don't have
real buckets (Lambda: region-bound filesystem S3 adapter only; Thunder:
per-instance disks only). R2 speaks the S3 API — every `aws s3` command below
works unchanged via `AWS_ENDPOINT_URL` — charges **zero egress** (nodes pulling
the dataset, laptops pulling checkpoints: free), and is neutral across any
node. All `s3://saage-runs/...` URIs in this document refer to this R2 bucket.

---

## 2. CLI surface

```bash
# one-time
saage remote init                      # creds file, ssh key, R2 bucket, dataset staging
saage remote add-target spark --host spark.local --user saage   # register a node

# the button
saage remote handoff contrib/lewm_hillclimb/flow.yaml --target spark \
    --set train_epochs=8               # all `saage run` flags pass through verbatim

# trust commands
saage remote status [RUN_ID]           # experiment 6/10, best 71.2, uptime
saage remote logs RUN_ID [--live]      # bucket log tail; --live ssh-tails
saage remote ps                        # probe all targets; reconcile vs local state
saage remote kill RUN_ID               # stop the run (never the box), final sync
saage remote fetch RUN_ID              # ledgers/report/best-ckpt → ./results/RUN_ID/
```

What `handoff` does, in order:

1. **Preflight** (abort cleanly before anything starts): target reachable over
   SSH with key auth; `nvidia-smi` shows a GPU; no saage run already on the
   box; R2 reachable; dataset staged; workspace packaging checks (§3.2); LLM
   key available for the flow's declared provider.
2. Generate `run_id` (`lewm-20260609-a3f2`), create `~/.saage/runs/<run_id>/`,
   write `manifest.json` (§3.5), mirror it to the bucket.
3. Push secrets (§4.3) and bootstrap the node (§6).
4. Start the run, detached:

   ```bash
   ssh node 'tmux new-session -d -s saage-<run_id> "bash ~/run/start.sh"'
   # start.sh wraps:  saage run ~/run/flow/flow.yaml --workspace ~/work/le-wm \
   #                    --set stablewm_home=~/stablewm --set <passthroughs...>
   ```

   `--workspace` already exists on `saage run` — it overrides the flow's
   hardcoded laptop path. **This is the entire extent of "adapting the flow
   for remote."**
5. Print `run lewm-20260609-a3f2 handed off — saage remote status` and exit.
   The laptop is now optional.

Flows may declare needs so preflight can check them:

```yaml
# flow.yaml — new optional block, ignored by local runs
compute: { gpu: a100, disk_gb: 200, max_run_days: 12 }
# v1: gpu/disk are preflight *checks* against the target, not provisioning specs
```

---

## 3. Workspace packaging: git ref + branch id

The brownfield wrinkle: `workspace: /home/cpadwick/code/le-wm` points at a
separate repo that the flow *mutates*. How does it travel?

**Decision: ship a git reference, not files.** The flow already *requires*
git — `setup_experiment.py` creates the branch + snapshot commit and
`keep_or_revert.py` runs `git checkout/clean/add/commit` per experiment — so
the node-side workspace must be a real repo no matter what. Given that:

- handoff records `{repo_url, base_sha}`; the node clones at `base_sha`
- the flow's own setup step creates the run branch
  `saage-hillclimb-<run_id>` (branch-per-run → concurrent handoffs are safe)
- the node pushes the branch after every kept experiment
- **results retrieval is solved as a side effect**: the durable artifact *is*
  the branch — `git fetch` from anywhere and you have every kept experiment

### 3.1 Branch naming

`setup_experiment.py` currently hardcodes `saage-hillclimb`. Smallest code
change in the whole plan: it takes (or reads from shared) a `--branch` /
`{{ run_branch }}` value; local runs default to `saage-hillclimb` as today.
This is a flow-helper edit, not an engine change.

### 3.2 Preflight checks (where "package it up" sneaks back in)

1. **Dirty working tree.** You'll often have uncommitted le-wm tweaks. Handoff
   must never silently ship `HEAD` while the tree differs — the run would test
   different code than what you're looking at. Behavior:

   ```
   $ saage remote handoff ...
   workspace /home/cpadwick/code/le-wm has uncommitted changes (3 files).
     [c] commit them onto the run branch as a handoff snapshot   (default)
     [a] abort
   ```

   "Commit onto run branch" = create `saage-hillclimb-<run_id>` locally from
   HEAD, commit the dirty state there, push it; the node clones that. Your
   local branch and working tree are untouched.

2. **No pushable remote → `git bundle` fallback** (not rsync/tarball — a
   bundle is the repo *as a repo*, so all the flow's git machinery works
   unchanged):

   ```bash
   git -C /home/cpadwick/code/le-wm bundle create /tmp/ws.bundle HEAD
   scp /tmp/ws.bundle node:~/run/  &&  ssh node 'git clone ~/run/ws.bundle ~/work/le-wm'
   ```

   With a bundle there's no push-out channel; the run's commits return as a
   reverse bundle at fetch time (`remote fetch` grabs
   `runs/<run_id>/final.bundle`, which the node writes at exit).

3. **Node→repo auth** for the push channel: a GitHub **fine-grained PAT
   scoped to the one repo, contents:write**, injected like every other node
   secret (§4.3), revoked after the run.

### 3.3 Greenfield flows

No `workspace` block at all — their workspace is ephemeral and created on the
node (`--workspace ~/work/run`). One manifest schema covers both; `workspace`
is simply optional. This keeps greenfield_ml as the cheap end-to-end test
vehicle (§8).

### 3.4 What git packaging deliberately misses: the ledgers

`experiments.jsonl` and `research_log.md` are *intentionally* gitignored (they
survive `git clean`). A git ref ships none of them and branch pushes return
none of them. Fresh runs don't care (the files are created during the run),
but it means **the bucket sync is load-bearing for ledgers, not just
checkpoints**. Corollary: a future *continuation* handoff seeds code from the
ref and ledgers from the bucket.

### 3.5 The manifest

Written to `~/.saage/runs/<run_id>/manifest.json` and mirrored to the bucket:

```json
{
  "run_id": "lewm-20260609-a3f2",
  "flow": "contrib/lewm_hillclimb/flow.yaml",
  "saage_ref": "git@github.com:cgpadwick/saage.git@9be01f2",
  "target": "spark",
  "set": { "train_epochs": 8, "target_success": 74.0 },
  "workspace": {
    "repo": "git@github.com:cgpadwick/le-wm.git",
    "base_sha": "3f9c2e1",
    "run_branch": "saage-hillclimb-lewm-20260609-a3f2",
    "dirty_tree": "committed"
  },
  "dataset": "s3://saage-runs/datasets/ogbench-cube/",
  "bucket_prefix": "s3://saage-runs/runs/lewm-20260609-a3f2/",
  "llm_provider": "openrouter"
}
```

---

## 4. Credentials

Principle: **every credential that leaves the laptop is scoped and per-run.**
With provisioning descoped, v1 needs **no provider API keys at all** — SSH key
auth to the node is the only infrastructure credential.

| Credential | Lives where | Goes to node? | Scope |
|---|---|---|---|
| Master LLM keys | laptop env vars, as today | never | — |
| **Per-run LLM key** | minted/recorded at handoff | **yes** | spend-capped, revoked after run |
| SSH keypair | `~/.saage/ssh/saage_ed25519`, generated at init | pubkey only (in node's authorized_keys) | dedicated to saage |
| R2 token | credentials.toml; copy to node | yes | one bucket, object read/write |
| Repo PAT | minted per run | yes | one repo, contents:write |

### 4.1 `~/.saage/credentials.toml`

```toml
# chmod 600 — created by `saage remote init`
[storage]
# Cloudflare R2 — S3-compatible; key is an R2 API token scoped to this bucket only
endpoint   = "https://<account-id>.r2.cloudflarestorage.com"
bucket     = "saage-runs"
access_key = "..."            # R2 token's S3 access key id
secret_key = "..."
region     = "auto"           # R2 convention

[openrouter]
provisioning_key = "sk-or-prov-..."   # optional: lets handoff mint per-run keys

[github]
pat_minting = "manual"   # v1: paste a fine-grained PAT per run when prompted

# ---- registered nodes ----
[targets.spark]                # DGX Spark on the LAN
host = "spark.local"
user = "saage"                 # dedicated unprivileged run user — see §4.4

[targets.lam1]                 # a Lambda box launched by hand in their dashboard
host = "150.136.41.7"
user = "ubuntu"
hourly_usd = 1.29              # optional: `status`/`ps` show uptime × $/hr as a reminder
```

Loader: env vars override file; refuse the file if group/other-readable:

```python
# saage/remote/creds.py
import os, stat, sys, tomllib
from pathlib import Path

CRED_PATH = Path.home() / ".saage" / "credentials.toml"

def load_creds() -> dict:
    creds: dict = {}
    if CRED_PATH.exists():
        mode = stat.S_IMODE(CRED_PATH.stat().st_mode)
        if mode & 0o077:
            sys.exit(f"refusing {CRED_PATH}: mode {oct(mode)}; run chmod 600 {CRED_PATH}")
        creds = tomllib.loads(CRED_PATH.read_text())
    for section, key, env in [
        ("storage", "access_key", "SAAGE_STORAGE_ACCESS_KEY"),
        ("storage", "secret_key", "SAAGE_STORAGE_SECRET_KEY"),
    ]:
        if os.environ.get(env):
            creds.setdefault(section, {})[key] = os.environ[env]
    return creds
```

### 4.2 The per-run LLM key (the one real cost of node-as-master)

The engine runs remotely, so an LLM key must too. Make its blast radius one
run's budget:

- **OpenRouter** (what lewm uses): the provisioning API can mint runtime keys
  with a credit limit. Handoff mints `saage-<run_id>` with e.g. a $40 cap,
  injects it as `OPENROUTER_API_KEY`, and **deletes it** in `remote kill` /
  on completion / in `remote ps` cleanup.
- **Anthropic**: use a dedicated workspace with a spend cap; rotate after runs.
- v1 fallback for any provider: prompt the user to paste a capped key at
  handoff time. On hardware you own, `llm_key = "reuse"` (push the laptop's
  own env key for the run) is an acceptable convenience.

Note: **agent-written code executes on the node** (training code the implement
agent edits). It runs as the same unprivileged user as the engine, so it can
read the per-run LLM/R2/repo keys — acceptable because all three are
capped/scoped — but nothing else (§4.4).

### 4.3 Pushing secrets to the node — safely

Never in images, never on command lines (`ps`-visible), never in anything a
provider metadata service could expose. SSH stdin → 0600 file:

```python
def push_run_secrets(ssh, secrets: dict[str, str]) -> None:
    env_file = "".join(f"{k}={v}\n" for k, v in secrets.items())
    # OPENROUTER_API_KEY, AWS_ACCESS_KEY_ID/SECRET, AWS_ENDPOINT_URL, GIT_TOKEN
    ssh.run("install -m 600 /dev/null ~/.saage_run_env && cat > ~/.saage_run_env",
            input=env_file)
    # AWS_ENDPOINT_URL = the R2 endpoint — aws-cli v2 reads it from the env,
    # so every `aws s3 ...` on the node talks to R2 with no flag changes.
```

The stop path (`remote kill`, normal completion) deletes `~/.saage_run_env`.
Nothing is stored on any node between runs, so a handoff to your DGX and a
handoff to a rented box are credential-identical.

### 4.4 Secrets on a box you own (DGX Spark et al.)

Same mechanism, same scoping — only the threat model moves. A rented node is
empty and disposable, but a personal box has **ambient credentials** (your
dotfiles, `gh` login, `~/.ssh` keys) — and agent-written code executes during
a run. The one hard rule:

> Hand off to a **dedicated unprivileged user** (`saage`, created once on the
> box), not your own account. The run then sees only the injected per-run
> secrets — the same blast radius as a rented node — instead of everything
> your account can reach.

Per credential:
- **GitHub**: never rely on a `gh` login on the box; the per-run one-repo PAT
  in the env file is the only repo credential the run gets.
- **R2**: the same bucket-scoped token everywhere — nothing LAN-specific.

### 4.5 What `remote init` verifies

SSH keypair generated; R2 bucket exists and is writable with the bucket-scoped
token (init walks through creating both in the Cloudflare dashboard if
missing); dataset staged at `s3://<bucket>/datasets/ogbench-cube/` (offers to
upload `~/.stable-wm`); LLM key minting path configured. `add-target` verifies
key auth, GPU, and creates the run user if asked. After init, **the button
never prompts** (except the dirty-tree question, §3.2).

---

## 5. Run state

### 5.1 Laptop: `~/.saage/runs/<run_id>/`

```
state.json        # snapshot (atomic tmp+rename writes)
manifest.json     # §3.5
events.jsonl      # append-only: preflight_ok, bootstrap_done, handoff_complete, killed...
handoff.log       # the handoff command's own output
```

`state.json`:

```json
{
  "run_id": "lewm-20260609-a3f2",
  "phase": "running",
  "target": "spark",
  "node": { "host": "spark.local", "user": "saage",
            "gpu": "GB10", "hourly_usd": 0.0 },
  "tmux_session": "saage-lewm-20260609-a3f2",
  "llm_key_id": "or-key-saage-lewm-20260609-a3f2",
  "started_at": "2026-06-09T17:18:30Z"
}
```

**Cardinal rule:** state files record *intent*; the node records *truth*;
`saage remote ps` reconciles. It probes every registered target over SSH for
`saage-*` tmux sessions and diffs against local run state, flagging both
directions:

```
RUN                  PHASE     TARGET   SESSION ON NODE   UPTIME   $/hr
lewm-20260609-a3f2   running   spark    saage-lewm-...    26.4h    0
(none)               —         lam1     saage-mnist-x9    41.2h    1.29  ⚠ ORPHAN — kill? [y/N]
```

`ps` also garbage-collects: runs whose sessions are gone get `phase: done|dead`
(by reading the bucket's final `status.json`), and their per-run LLM keys get
revoked if still live. For targets with `hourly_usd` set, `ps`/`status` print
the running cost — with provisioning descoped, **terminating a rented box is
the user's job**, so the reminder is the guardrail.

### 5.2 Bucket (Cloudflare R2): `s3://saage-runs/runs/<run_id>/`

```
manifest.json
status.json            # written by node sidecar: phase, last heartbeat
ledgers/               # experiments.jsonl, research_log.md  (the gitignored channel, §3.4)
logs/saage.log         # engine log (rolling tail)
ckpt/<exp>/            # parked checkpoints
report/                # report_narrative.md / report.html at completion
final.bundle           # only in bundle-mode (§3.2)
```

This is the rendezvous: `remote status`/`logs`/`fetch` read the bucket — **no
SSH required**, works from any machine with the creds file, works while your
laptop is off the LAN, works post-mortem. (A `sync = "off"` per-target option
exists for fully-offline boxes; `status`/`logs` then fall back to SSH.)

---

## 6. The target: any SSH-able host with a GPU

v1 has exactly one backend. The `Backend` protocol from the earlier draft
survives as the seam provisioning will plug into later (§9), but today it has
one implementation:

```python
# saage/remote/ssh_target.py
class SshTarget:
    def __init__(self, name: str, host: str, user: str,
                 hourly_usd: float = 0.0, sync: bool = True): ...

    def preflight(self, run_id: str) -> None:
        ssh_check(self.host, self.user)        # key auth works
        assert_gpu(self.host)                  # nvidia-smi present
        assert_not_busy(self.host)             # refuse if a saage-* session exists (v1: one run per box)

    def stop_run(self, run_id: str) -> None:
        # stop the RUN, never the box: kill tmux session, final sync,
        # delete ~/.saage_run_env. Never shutdown/terminate — v1 doesn't own machines.
        ...

    def list_sessions(self) -> list[str]: ...  # for `remote ps`
```

`localhost` is a valid target — which makes the entire handoff path testable
with zero hardware and zero spend, and is how Phase 1 is developed.

### 6.1 Bootstrap (`bootstrap.sh`, run once over SSH)

```bash
#!/usr/bin/env bash
set -euo pipefail
source ~/.saage_run_env                          # per-run secrets (§4.3)

curl -LsSf https://astral.sh/uv/install.sh | sh
git clone --depth 1 --branch "$SAAGE_REF" "$SAAGE_REPO" ~/run/saage
cd ~/run/saage && uv venv && uv pip install -e .

# workspace: clone the run branch (or from ws.bundle in bundle mode)
git clone --branch "$RUN_BRANCH" \
  "https://x-access-token:${GIT_TOKEN}@github.com/cgpadwick/le-wm.git" ~/work/le-wm
cd ~/work/le-wm && <le-wm env setup: uv/poetry per its README>

# dataset: bucket → node (datacenter/LAN bandwidth; laptop never in the data path)
mkdir -p ~/stablewm
aws s3 sync "$DATASET_URI" ~/stablewm/ --only-show-errors

echo BOOTSTRAP_OK
```

On a personal box, bootstrap is idempotent and cached (saage repo, le-wm env,
dataset already present from last run → seconds, not minutes).

### 6.2 Run wrapper (`start.sh`, launched in tmux)

```bash
#!/usr/bin/env bash
set -uo pipefail
source ~/.saage_run_env
B="$BUCKET_PREFIX"   # s3://saage-runs/runs/<run_id>/

sync_out() {  # ledgers + logs + status heartbeat; idempotent, cheap
  aws s3 cp ~/work/le-wm/experiments.jsonl "$B/ledgers/" --only-show-errors 2>/dev/null
  aws s3 cp ~/work/le-wm/research_log.md  "$B/ledgers/" --only-show-errors 2>/dev/null
  aws s3 cp ~/run/saage.log               "$B/logs/"    --only-show-errors
  printf '{"phase":"%s","updated":"%s"}\n' "$1" "$(date -u +%FT%TZ)" > /tmp/status.json
  aws s3 cp /tmp/status.json "$B/status.json" --only-show-errors
  (cd ~/work/le-wm && git push -q origin "$RUN_BRANCH" 2>/dev/null) || true
}

( while true; do sync_out running; sleep 300; done ) &   # sidecar
SIDECAR=$!
# watchdog: a wedged run can't hold the GPU forever (stops the RUN, not the box)
( sleep $(( MAX_RUN_DAYS * 86400 )); sync_out timeout; pkill -f "saage run" ) &

cd ~/run/saage && source .venv/bin/activate
saage run ~/run/flow/flow.yaml --workspace ~/work/le-wm \
  --set run_branch="$RUN_BRANCH" --set stablewm_home=~/stablewm \
  $SAAGE_SET_ARGS  2>&1 | tee ~/run/saage.log
RC=$?

kill $SIDECAR
aws s3 sync ~/stablewm/checkpoints/ "$B/ckpt/" --only-show-errors   # park best ckpts
aws s3 cp ~/work/le-wm/report_narrative.md "$B/report/" --only-show-errors 2>/dev/null
[ $RC -eq 0 ] && sync_out done || sync_out failed
shred -u ~/.saage_run_env          # secrets do not outlive the run
```

---

## 7. Observability

- `saage remote status` reads `status.json` + `ledgers/experiments.jsonl` from
  the bucket and renders: phase, experiment k/N, candidate vs best score,
  uptime (× $/hr when the target declares a price). No SSH; works post-mortem.
- **Stale heartbeat = the alarm.** `updated` older than ~15 min while the
  node's session is alive ⇒ hung run; `status` flags it and suggests
  `remote logs --live` / `remote kill`.
- Optional later: sidecar posts a webhook (ntfy/Slack) on `done|failed|timeout`.

## 7.1 Failure matrix — what the button owns

| Failure | Detection | Response |
|---|---|---|
| Preflight fails (ssh/GPU/busy/dirty tree/R2) | before anything starts | abort; nothing touched |
| Bootstrap fails | non-zero exit over SSH | surface log, `phase: failed`, secrets file removed |
| Train crashes (bad candidate) | flow's own semantics | `candidate_score=-1` → revert; run continues. Unchanged from local |
| Engine/flow errors out | `start.sh` RC ≠ 0 | final sync → `status: failed` → secrets shredded; session ends |
| Node dies / reboots mid-run | heartbeat stale + `ps` finds no session | artifacts up to last sync are in bucket + branch. v1: re-handoff is manual; v2: `remote resume` seeds from bucket + branch |
| Run hangs | fresh-heartbeat-but-no-progress / watchdog | watchdog kills the run (not the box), final sync |
| Laptop dies | — | irrelevant: node is the master; state recoverable from bucket manifest |
| `remote kill` | command | stop session → final sync → revoke per-run LLM key → `phase: killed` |
| Flow completes | RC 0 | sync + report + branch push → secrets shredded → key revoked on next `ps`/`status` |
| Forgotten rented box | `ps`/`status` show uptime × $/hr | **user terminates it** — v1 doesn't own machines; provisioning phase (§9) automates this |

---

## 8. Build phases

1. **Plumbing + ssh target, zero hardware:** `saage/remote/` — `creds.py`,
   `state.py` (atomic state.json + events.jsonl), manifest, preflight (incl.
   dirty-tree + bundle fallback), `ssh_target.py`, bootstrap/start scripts,
   `handoff`/`status`/`kill`/`ps`/`fetch` — all developed against
   `localhost` as the target. Testable offline, in CI, alongside the existing
   suite.
2. **DGX Spark + greenfield e2e:** `add-target spark`, dedicated `saage` user,
   run greenfield_ml end-to-end (hours, free). Deliberately test: kill the run
   mid-train, `remote kill`, watchdog firing, dirty-tree preflight, orphan
   detection via `ps`, laptop-off-LAN status via bucket.
3. **lewm_hillclimb for real:** the `--branch` param in `setup_experiment.py`
   (§3.1), per-run OpenRouter key minting, one full run — on the Spark, or on
   a hand-launched Lambda box registered as a target (same code path).
4. **Provisioning (deferred from v1 — §9):** Lambda backend (launch/terminate/
   capacity fallback via their REST API), then Thunder (via `tnr` CLI), plus
   the self-terminate machinery rented nodes need. Pure addition: a
   provisioned node ends up as an ssh target handoff.
5. **Quality of life:** `remote resume` (seed: branch + bucket ledgers — the
   two-channel model makes this tractable), completion webhook, multi-run-per-
   box scheduling, k-parallel proposal training across targets.

**Engine changes required: none.** Flow changes required: one optional
`compute:` block and the `run_branch` parameter in one helper script.

---

## 9. Auto-provisioning — Lambda implemented (2026-06-09), Thunder deferred

**Status:** `saage remote spawn` / `saage remote terminate` are implemented
for Lambda Cloud (`saage/remote/lambda_api.py`) and verified with a full live
loop: spawn (A10, us-east-1) → handoff greenfield_ml → done (acc 0.9851 in
~4.5 min) → fetch → terminate → account empty. Provisioning proved strictly
*additive* as designed: spawn just registers a normal ssh target. Field
notes: the API sits behind a Cloudflare WAF that 403s default urllib
user-agents (error 1010); launch takes exactly one ssh key name, so spawn
launches with the saage key and appends other account keys to authorized_keys
afterwards; a launch that never reaches `active` is terminated, never leaked.
Original design notes follow.

- **Backend protocol:** `launch(spec) -> Node`, `terminate(id)`,
  `list_nodes()`, `wait_ssh(node)` — the seam already exists in §6.
- **Lambda Cloud:** REST API, Bearer auth (`cloud.lambda.ai/api/v1`):
  `GET /instance-types` for capacity, `POST /instance-operations/launch` with
  `instance_type_name`/`region_name`/`ssh_key_names`, poll
  `GET /instances/{id}` until `active`, `POST /instance-operations/terminate`.
  GPU-class → instance-type preference map with region fallback; terminate
  half-launched instances on timeout (never leak a node). The earlier draft
  (`docs/cloud_one_button_plan.md`) has a full code sketch.
- **Thunder Compute:** wrap their `tnr` CLI with subprocess. Confirm first:
  virtualized-GPU training perf parity; whether plain ssh/rsync works or
  everything must route through `tnr connect`/`tnr scp`.
- **Self-termination for rented nodes:** on Lambda an OS shutdown does NOT
  stop billing — termination must go through the provider API, so the node
  needs *just enough* provider credential to terminate itself. Design:
  provider key root-only on the node, `ubuntu` gets a single sudoers-allowed
  `saage-terminate-self` command. Agent-written code can *trigger* termination
  (fails safe) but can't *read* the account key. Until this lands, the §7.1
  "forgotten box" reminder is the guardrail.
- **Per-run provider keys do not exist** (Lambda/Thunder have account-level
  keys only) — another reason provisioning stays out of v1's credential story.

---

## 10. Open questions

1. **OpenRouter provisioning API** — confirm runtime-key minting + credit
   limits + revocation endpoints before relying on it (v1 fallback: paste a
   capped key at handoff).
2. **le-wm env setup on the node** — what exactly `bootstrap.sh` runs for
   le-wm's deps (poetry? uv? CUDA wheel pinning); encode it once, or give
   flows an optional `bootstrap:` hook in the `compute:` block.
3. **DGX Spark arch** — Spark is aarch64 (GB10); confirm le-wm's CUDA wheel
   stack installs cleanly on arm64, or the Spark target needs its own wheel
   pins in the bootstrap.
4. **Heartbeat-while-training** — the sidecar heartbeats independently of the
   engine, so a wedged `train.py` still looks "running" until the watchdog.
   Nice-to-have: sidecar also ships the last lines of the active training log
   so `status` can show per-epoch progress.
5. **R2 token granularity** — confirm the bucket-scoped API token flow
   (object read/write on one bucket, no admin) and whether `remote init` can
   create the bucket via API with a user-supplied account token, or must
   instruct the user through the dashboard.
6. **`compute:` preflight on heterogeneous boxes** — what "gpu: a100" means
   when the target is a Spark (GB10) or a 4090: warn vs refuse; probably
   `--force` to override.

---

## 12. Implementation status (2026-06-09)

v1 is implemented in `saage/remote/` (creds, state, sshio, workspace, scripts,
target, handoff, observe, cli) with offline unit tests plus ssh-gated
integration tests (`SAAGE_SSH_TESTS=1 pytest tests/remote/`). Deltas from the
plan above, decided during the build:

- **The artifact store is two-tier.** The node-side run dir is primary: the
  sidecar copies ledgers/results into `~/.saage_runs/<run_id>/artifacts/`,
  and `status`/`logs`/`fetch` read it over SSH. When a `[storage]` section is
  configured (Cloudflare R2, bucket `saage-data`), the sidecar additionally
  mirrors `artifacts/` + `status.json` + `saage.log` to
  `s3://<bucket>/runs/<run_id>/` via `python -m saage.remote.r2push` (boto3,
  installed into the run venv only when storage is configured), and the
  laptop falls back to the mirror automatically when the node is unreachable
  (`status`, `fetch`; or explicitly with `fetch --bucket`). With no
  `[storage]`, everything works SSH-only.
- **Engine source travels by rsync of the laptop checkout**, not `git clone`
  of a pinned ref — works for unpushed branches and needs no saage repo
  credential on the node. The manifest does not yet record a saage sha.
- **LLM key is "reuse" mode only**: the laptop's provider env var (per the
  flow's `provider.type`) is pushed into the per-run `run_env`. Per-run capped
  keys (§4.2) remain future work.
- **Run branches are `saage-run-<run_id>`** for every flow (plan said
  flow-specific names). The lewm `setup_experiment.py --branch` param (§3.1)
  is implemented: `start.sh` passes `--set run_branch=$WS_RUN_BRANCH`, the
  flow forwards it as `--branch`, so kept-experiment commits land on the
  branch the node pushes back (locally it defaults to `saage-hillclimb`).
- **The sidecar pushes the run branch on every sync** (not only at exit), so
  a node death loses at most one sync interval of kept commits.
- **The repo PAT never persists on the node**: the token-bearing URL lives
  only in the per-run `run_env` (0600, deleted at run end) and is used
  per-operation; `ws/.git/config` always holds the clean URL.
- **`--bootstrap-timeout`** caps node bootstrap (default 1800 s) — raise it
  when `--ws-setup` stages a large dataset (the lewm cube download is 46 GB).
- **Flows declare their artifacts**: an optional `artifacts:` list in
  flow.yaml (workspace-relative filenames/globs) tells the sidecar what to
  collect; the old hardcoded ledger names are only the fallback. Keeps
  flow-specific naming out of the library; local runs ignore the key.
- **Node layout**: `~/.saage_runs/<run_id>/{saage,venv,flow,ws,artifacts,...}`
  per run; tmux session `saage-<run_id>`; one run per box enforced by
  preflight (any `saage-*` session = busy).
- **Bundle mode is one-way for commits in v1**: the node clones the bundle,
  but with no pushable origin the run's *commits* don't come back yet
  (`final.bundle` at exit is unimplemented) — ledgers/artifacts still return
  via fetch. Branch mode round-trips commits fully (integration-tested
  against a local bare origin).
- **`SAAGE_GIT_TOKEN`** is honored (rewrites an https origin URL for the
  node's clone/push) but has not been exercised against real GitHub yet.
- **Engine changes required: none** held true — `saage/cli.py` only gained
  the `remote` subcommand dispatch; `tomli` was added as a <3.11 dependency.
- **Remote resume + R2 checkpoint/model mirroring** are now implemented
  (`saage remote resume <run> [--target <box>]`): the sidecar mirrors the
  engine checkpoint and any flow `artifacts:` entries to R2 on each sync
  (changed-only), enabling cross-box resume from the R2 checkpoint when the
  original node is gone.
