#!/usr/bin/env bash
# One-time node setup for the lewm_hillclimb flow on a cloud GPU box.
# Run from inside the le-wm workspace clone (saage handoff does this via
# `--ws-setup "bash ../flow/cloud_setup.sh"`); idempotent, so re-runs after
# the first are fast (env check + dataset presence check only).
#
# Layers:
#   1. torch/CUDA base from cgpadwick/ml-frameworks — the curated, CI-tested
#      stack repo. The stack is chosen by what the node's DRIVER supports
#      (this is what bit us on 2026-06-10: PyPI torch ships +cu130, Lambda
#      images run r570 = CUDA <= 12.8, torch saw no GPU).
#   2. stable-worldmodel pinned to the git SHA the working env uses (the
#      PyPI release lags it). Its bare `torch` requirement is satisfied by
#      the stack, so the curated pin survives.
#   3. EGL/GL libs — eval.py renders mujoco headlessly; cloud images often
#      ship with no GL stack at all.
#   4. OGBench-Cube dataset from the public HF repo quentinll/lewm-cube
#      (46GB .tar.zst -> ~95GB .h5). Needs ~150GB free during extract.
#
# Env flags: SAAGE_SKIP_DATASET=1 skips step 4 (env-only validation).
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
STABLEWM_HOME="${STABLEWM_HOME:-$HOME/.stable-wm}"

SWM_REF="abdced49809d5eae38e24b27dc7b635c502c4812"    # stable-worldmodel pin
MLFW_REF="2791ecbbde4b2699d3751b19cbd77bb166fdff6a"   # ml-frameworks pin (master 2026-06-10)
MLFW_URL="https://github.com/cgpadwick/ml-frameworks.git"

# ---- pick the stack the driver can actually run -------------------------------
DRIVER_MAJOR="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | cut -d. -f1)"
if   [ "$DRIVER_MAJOR" -ge 580 ]; then STACK=pytorch-cu130   # CUDA 13 capable (incl. GB10)
elif [ "$DRIVER_MAJOR" -ge 560 ]; then STACK=pytorch-cu126
elif [ "$DRIVER_MAJOR" -ge 530 ]; then STACK=pytorch-cu121
else                                   STACK=pytorch-cu118
fi
echo "driver r${DRIVER_MAJOR} -> stack ${STACK}"

# ---- build prerequisites -------------------------------------------------------
# stable-worldmodel -> gymnasium[all] -> box2d-py builds from source and needs
# swig + a C toolchain. ML-flavored images (Lambda) ship them; bare templates
# (Thunder 'base') don't.
if ! command -v swig >/dev/null 2>&1 || [ ! -f /usr/include/python3.10/Python.h ]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq swig build-essential python3-dev
fi

# ---- python env: ml-frameworks base + stable-worldmodel on top ----------------
if [ ! -e .venv/bin/python ]; then
  uv venv --python=3.10 .venv
fi
if ! .venv/bin/python -c "import torch" 2>/dev/null; then
  CACHE="$HOME/.cache/saage"
  mkdir -p "$CACHE"
  if [ ! -d "$CACHE/ml-frameworks" ]; then
    git clone --quiet "$MLFW_URL" "$CACHE/ml-frameworks"
  fi
  git -C "$CACHE/ml-frameworks" fetch -q origin "$MLFW_REF" 2>/dev/null || true
  git -C "$CACHE/ml-frameworks" checkout -q "$MLFW_REF"
  VIRTUAL_ENV="$PWD/.venv" uv pip install --quiet poetry
  ( cd "$CACHE/ml-frameworks/stacks/$STACK" \
    && VIRTUAL_ENV="$OLDPWD/.venv" PATH="$OLDPWD/.venv/bin:$PATH" \
       poetry config virtualenvs.create false --local \
    && VIRTUAL_ENV="$OLDPWD/.venv" PATH="$OLDPWD/.venv/bin:$PATH" \
       poetry install --no-root --quiet )
fi
VIRTUAL_ENV="$PWD/.venv" uv pip install --quiet \
  "stable-worldmodel[train,env] @ git+https://github.com/galilai-group/stable-worldmodel.git@${SWM_REF}" \
  "huggingface_hub[cli,hf_transfer]" \
  trackio \
  hdf5plugin \
  datasets \
  "stable-pretraining==0.1.6" \
  "lightning==2.6.4"
# stable-pretraining/lightning pinned to the known-good local env: an
# unconstrained resolve once picked stable-pretraining 0.1.4, whose
# on_train_start calls len() on a bare optimizer -> TypeError at train start.
# trackio: train.py imports it directly; not a stable-worldmodel dep.
# hdf5plugin: without it the hdf5 Format plugin silently fails to register
# and load_dataset reports the misleading "No format detected" for the .h5.
# datasets: stable_pretraining imports it (data/datasets.py) without
# declaring it; the old fatter resolution happened to include it.
.venv/bin/python - <<'PY'
import torch
assert torch.cuda.is_available(), (
    "torch sees no CUDA device — driver/stack mismatch? "
    f"(torch {torch.__version__}, built for cuda {torch.version.cuda})")
print(f"env ok: torch {torch.__version__}, cuda {torch.version.cuda}, "
      f"device {torch.cuda.get_device_name(0)}")
PY

# ---- headless GPU rendering ----------------------------------------------------
# eval.py renders mujoco via EGL (it sets MUJOCO_GL=egl itself); cloud images
# often ship without any GL stack -> PyOpenGL dies with
# "'NoneType' object has no attribute 'eglQueryString'".
if ! ldconfig -p | grep -q libEGL; then
  sudo apt-get update -qq            # fresh images may have empty package lists
  sudo apt-get install -y -qq libegl1 libgl1 libglvnd0 libopengl0 libgles2
fi

# ---- dataset --------------------------------------------------------------------
if [ -z "${SAAGE_SKIP_DATASET:-}" ]; then
  TARGET="$STABLEWM_HOME/datasets/ogbench/cube_single_expert.h5"
  mkdir -p "$STABLEWM_HOME/datasets/ogbench" "$STABLEWM_HOME/checkpoints"
  if [ ! -f "$TARGET" ]; then
    command -v zstd >/dev/null 2>&1 || { sudo apt-get update -qq; sudo apt-get install -y -qq zstd; }
    SCRATCH="$STABLEWM_HOME/_staging"        # same filesystem as TARGET -> mv is free
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

  # warm the page cache in the background — boxes with RAM > dataset size (an
  # A100 node has ~200GiB) then serve epoch-1 reads from memory instead of disk
  nohup cat "$TARGET" > /dev/null 2>&1 &
fi

echo CLOUD_SETUP_OK
