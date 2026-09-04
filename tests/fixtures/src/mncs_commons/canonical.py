"""Test double: owner Commons canonical digest contract (see package docstring)."""
from __future__ import annotations

import hashlib
import json


def canonical_digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
    ).hexdigest()
