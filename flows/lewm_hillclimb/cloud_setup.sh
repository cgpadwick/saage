#!/usr/bin/env bash
# One-time node setup for the lewm_hillclimb flow on a cloud GPU box.
# Run from inside the le-wm workspace clone (saage handoff does this via
# `--ws-setup "bash ../flow/cloud_setup.sh"`); idempotent, so re-runs after
# the first are fast (env check + dataset presence check only).
#
# What it does:
#   1. le-wm python env: uv venv (py3.10) + stable-worldmodel pinned to the
#      git SHA the working (local) environment uses — the PyPI release lags it.
#   2. OGBench-Cube dataset: pulled from the public HF dataset repo
#      quentinll/lewm-cube (46GB .tar.zst -> ~95GB .h5) into
#      $STABLEWM_HOME/datasets/ogbench/. Needs ~150GB free during extract.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
STABLEWM_HOME="${STABLEWM_HOME:-$HOME/.stable-wm}"

SWM_REF="abdced49809d5eae38e24b27dc7b635c502c4812"   # matches the working env

# ---- python env (README recipe + SHA pin) -----------------------------------
if [ ! -e .venv/bin/python ]; then
  uv venv --python=3.10 .venv
fi
VIRTUAL_ENV="$PWD/.venv" uv pip install --quiet \
  "stable-worldmodel[train,env] @ git+https://github.com/galilai-group/stable-worldmodel.git@${SWM_REF}" \
  "huggingface_hub[cli,hf_transfer]"
.venv/bin/python - <<'PY'
import torch
assert torch.cuda.is_available(), "torch sees no CUDA device"
print(f"env ok: torch {torch.__version__}, cuda {torch.version.cuda}, "
      f"device {torch.cuda.get_device_name(0)}")
PY

# ---- dataset -----------------------------------------------------------------
TARGET="$STABLEWM_HOME/datasets/ogbench/cube_single_expert.h5"
mkdir -p "$STABLEWM_HOME/datasets/ogbench" "$STABLEWM_HOME/checkpoints"
if [ ! -f "$TARGET" ]; then
  command -v zstd >/dev/null 2>&1 || sudo apt-get install -y -qq zstd
  SCRATCH="$STABLEWM_HOME/_staging"          # same filesystem as TARGET -> mv is free
  mkdir -p "$SCRATCH"
  echo "downloading cube dataset (46GB compressed) from HF…"
  HF_HUB_ENABLE_HF_TRANSFER=1 .venv/bin/hf download quentinll/lewm-cube \
      cube_single_expert.tar.zst --repo-type dataset --local-dir "$SCRATCH"
  echo "extracting…"
  tar --zstd -xf "$SCRATCH/cube_single_expert.tar.zst" -C "$SCRATCH"
  FOUND="$(find "$SCRATCH" -name 'cube_single_expert.h5' | head -1)"
  [ -n "$FOUND" ] || { echo "extract finished but cube_single_expert.h5 not found"; exit 1; }
  mv "$FOUND" "$TARGET"
  rm -rf "$SCRATCH"
fi
echo "dataset ok: $(du -h "$TARGET" | cut -f1) at $TARGET"
echo CLOUD_SETUP_OK
