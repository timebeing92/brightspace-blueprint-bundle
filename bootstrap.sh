#!/usr/bin/env bash
# Create the bundle's virtual environment and install dependencies.
# macOS / Linux. Run once after unzipping the bundle:
#     bash bootstrap.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "error: '$PY' not found. Install Python 3.11+ and re-run (or set PYTHON=...)." >&2
  exit 1
fi

VER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "Using $PY ($VER)"

if [ ! -d ".venv" ]; then
  echo "Creating .venv ..."
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

echo ""
echo "Done. The environment is ready."
echo "Next:  bash run_blueprint.sh /path/to/course-export.zip"
