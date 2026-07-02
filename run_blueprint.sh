#!/usr/bin/env bash
# Convenience wrapper: turn a Brightspace export into a flat-file blueprint.
#
#   bash run_blueprint.sh /path/to/course-export.zip
#   bash run_blueprint.sh /path/to/unpacked/export --course-number "ABC 123" \
#                         --course-title "Course Title" --term "Fall 2026"
#
# Any extra flags are passed straight through to build_blueprint_bundle.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if [ "$#" -lt 1 ]; then
  echo "usage: bash run_blueprint.sh <export.zip|unpacked-dir> [extra args]" >&2
  exit 2
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "No virtual environment found. Running bootstrap.sh first ..."
  bash bootstrap.sh
fi

.venv/bin/python scripts/build_blueprint_bundle.py "$@"
