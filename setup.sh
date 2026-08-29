#!/bin/sh
# saage installer (Linux / macOS / WSL2): create .venv/, install saage with the
# web UI + dev extras, and run `saage doctor`. Idempotent — safe to re-run.
# Uses uv when available (fast), plain python3 venv + pip otherwise.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "setup.sh: python3 not found — install Python >= 3.10 first" >&2
    exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "setup.sh: saage needs Python >= 3.10, found $(python3 -V)" >&2
    exit 1
}

if command -v uv >/dev/null 2>&1; then
    [ -d .venv ] || uv venv
    uv pip install -e ".[dev,server]"
else
    [ -d .venv ] || python3 -m venv .venv
    ./.venv/bin/pip install --quiet --upgrade pip
    ./.venv/bin/pip install --quiet -e ".[dev,server]"
fi

echo
./.venv/bin/saage doctor || true    # warnings (no key yet) are expected here
echo
echo "installed. next steps:"
echo "  source .venv/bin/activate"
echo "  saage setup                              # choose a provider, paste an API key"
echo "  saage run flows/story_writer/flow.yaml   # first live run"
echo "  saage serve                              # web UI at http://127.0.0.1:8321"
echo "  pytest -q                                # full offline test suite"
