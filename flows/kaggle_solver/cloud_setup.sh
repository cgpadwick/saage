#!/usr/bin/env bash
# One-time node setup for the kaggle_solver flow on a cloud GPU box.
# Invoked by `saage remote handoff --ws-setup "bash ../flow/cloud_setup.sh"`,
# which runs it from inside the flow's workspace (cwd = ws/, flow dir at
# ../flow). Idempotent: re-runs after the first are fast (env + data presence
# checks only), so a failed/re-tried handoff does not redo the slow steps.
#
# Why this exists: mle-bench + its competition data must be installed on the
# node BEFORE the flow's `prepare` step runs (prepare_comp.py only sanity-checks
# that prepared data already exists — it does not download). Doing it inline via
# --ws-setup was brittle; this is the real, versioned setup script.
#
# Layers:
#   1. git-lfs — mle-bench stores competition checksums/answers in Git-LFS;
#      without it `mlebench prepare` fails on missing/zero-byte objects.
#   2. a workspace .venv the engine auto-activates for EVERY command step
#      (saage/tools.py venv_env activates <workspace>/.venv once it exists), so
#      mle-bench + the training stack must live here, not in system python.
#   3. torch matched to the node's DRIVER (PyPI's default wheel can be built for
#      a newer CUDA than the Lambda driver supports -> torch sees no GPU).
#   4. mle-bench: clone + `pip install .` NON-editable — its PEP517 backend has
#      no build_editable hook, so `pip install -e .` errors out.
#   5. competition data via `mlebench prepare` (classic kaggle.json creds in
#      KAGGLE_USERNAME/KAGGLE_KEY work with mle-bench's pinned kaggle<1.7).
#
# Env knobs (passed through handoff run_env):
#   COMP                competition id (default: spooky-author-identification)
#   MLEBENCH_DATA_DIR   data root (default: $HOME/.mlebench/data)
#   KAGGLE_USERNAME / KAGGLE_KEY   kaggle auth (required for the data download)
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

COMP="${COMP:-spooky-author-identification}"
DATA_DIR="${MLEBENCH_DATA_DIR:-$HOME/.mlebench/data}"

# ---- git-lfs -------------------------------------------------------------------
if ! command -v git-lfs >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq git-lfs
fi
git lfs install --skip-repo

# ---- kaggle creds file (mle-bench's kaggle<1.7 reads either env or the file) ---
if [ ! -f "$HOME/.kaggle/kaggle.json" ] && [ -n "${KAGGLE_USERNAME:-}" ]; then
  mkdir -p "$HOME/.kaggle"
  printf '{"username":"%s","key":"%s"}\n' "$KAGGLE_USERNAME" "${KAGGLE_KEY:-}" \
    > "$HOME/.kaggle/kaggle.json"
  chmod 600 "$HOME/.kaggle/kaggle.json"
fi

# ---- workspace venv (engine auto-activates ws/.venv for command steps) ---------
if [ ! -e .venv/bin/python ]; then
  uv venv --python=3.11 .venv
fi

# ---- torch matched to the driver + the baseline data-science stack -------------
# The hill-climb agents write train.py against this base (they may `pip install`
# more via run_command — that lands here because the engine activates .venv).
if ! .venv/bin/python -c "import torch" 2>/dev/null; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    DRIVER_MAJOR="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | cut -d. -f1)"
  else
    DRIVER_MAJOR=0
  fi
  if   [ "$DRIVER_MAJOR" -ge 570 ]; then TORCH_IDX="https://download.pytorch.org/whl/cu128"
  elif [ "$DRIVER_MAJOR" -ge 560 ]; then TORCH_IDX="https://download.pytorch.org/whl/cu126"
  elif [ "$DRIVER_MAJOR" -ge 545 ]; then TORCH_IDX="https://download.pytorch.org/whl/cu124"
  elif [ "$DRIVER_MAJOR" -ge 525 ]; then TORCH_IDX="https://download.pytorch.org/whl/cu121"
  elif [ "$DRIVER_MAJOR" -ge 1   ]; then TORCH_IDX="https://download.pytorch.org/whl/cu118"
  else                                   TORCH_IDX="https://download.pytorch.org/whl/cpu"
  fi
  echo "driver r${DRIVER_MAJOR} -> torch index ${TORCH_IDX}"
  VIRTUAL_ENV="$PWD/.venv" uv pip install --quiet torch --index-url "$TORCH_IDX"
fi
VIRTUAL_ENV="$PWD/.venv" uv pip install --quiet numpy pandas scikit-learn

# ---- mle-bench: clone + non-editable install into .venv ------------------------
if ! .venv/bin/python -c "import mlebench" 2>/dev/null; then
  CACHE="$HOME/.cache/saage"
  mkdir -p "$CACHE"
  if [ ! -d "$CACHE/mle-bench/.git" ]; then
    rm -rf "$CACHE/mle-bench"
    git clone --quiet https://github.com/openai/mle-bench.git "$CACHE/mle-bench"
  fi
  git -C "$CACHE/mle-bench" lfs pull            # materialize LFS content BEFORE install
  # NON-editable: mle-bench's build backend lacks the PEP660 build_editable hook.
  VIRTUAL_ENV="$PWD/.venv" uv pip install --quiet "$CACHE/mle-bench"
fi

# ---- competition data ----------------------------------------------------------
# prepare_comp.py checks $DATA_DIR/$COMP/prepared/public — mirror that here.
if [ ! -d "$DATA_DIR/$COMP/prepared/public" ]; then
  echo "preparing competition data for ${COMP} (requires accepted rules on kaggle.com)…"
  .venv/bin/mlebench prepare -c "$COMP" --data-dir "$DATA_DIR"
fi

echo CLOUD_SETUP_OK
