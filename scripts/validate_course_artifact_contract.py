#!/usr/bin/env python3
"""Validate coursecraft activities, structure, or run identity JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from course_artifact_contracts import ContractIssue, validate_contract, verify_run_identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", type=Path)
    parser.add_argument("--mode", choices=("inspect", "transform"), default="transform")
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=None,
        help="For coursecraft.run/1, also verify emitted-file checksums under this directory.",
    )
    args = parser.parse_args(argv)

    error_count = 0
    for path in args.records:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"{path}: ERROR unreadable_json: {exc}")
            error_count += 1
            continue
        issues = validate_contract(payload, mode=args.mode)
        if payload.get("schema") == "coursecraft.run/1" and args.bundle_dir is not None:
            issues.extend(
                ContractIssue("error", "artifact_integrity", value)
                for value in verify_run_identity(payload, args.bundle_dir.resolve())
            )
        print(f"{path}: {payload.get('schema', '(no schema)')}")
        if not issues:
            print("  OK")
        for issue in issues:
            print(f"  {issue.render()}")
            if issue.severity == "error":
                error_count += 1
    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
