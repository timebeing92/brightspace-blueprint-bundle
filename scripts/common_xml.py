#!/usr/bin/env python3
"""Shared XML helpers for the extraction/generation/validation scripts.

These two helpers were defined identically in seven scripts; they live here now
so there is one definition to read and maintain. Import them from a sibling
script with ``from common_xml import local_name, clean`` — because every script
is invoked as ``python3 scripts/<name>.py`` (including under pytest, which shells
out via subprocess), the ``scripts/`` directory is on ``sys.path[0]`` and the
bare import resolves without any package machinery.

Note: ``build_quiz_package_from_workbook.py`` intentionally keeps its own
``clean`` — it additionally strips the ``.0`` suffix openpyxl adds to integer
cells, which is workbook-specific behavior and must not leak into XML parsing.
"""
from __future__ import annotations


def local_name(tag: str) -> str:
    """Return an XML tag without its ``{namespace}`` prefix."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def clean(value: object) -> str:
    """Coerce a value to a stripped string; ``None`` becomes ``""``."""
    return "" if value is None else str(value).strip()
