"""Test double for the owner-native Commons package boundary.

Plumbing only: these doubles implement the *interface shape* the
orchestrator relies on (``validate_record`` report protocol and
``canonical_digest`` determinism contract) so wiring can be tested
without an owner checkout. They assert NOTHING about Commons
semantics; semantic proof comes from the real end-to-end run against
the exact Commons revision.
"""


def _digest(obj):
    import hashlib
    import json

    return "sha256:" + hashlib.sha256(
        json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")
    ).hexdigest()
