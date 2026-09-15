# Selective family verification

`scripts/selective_family_verify.py` is the Actions-owned execution boundary
for a RAVEL plan whose routing scope is `selected_repositories`.  It loads the
content-addressed plan and Commons graph, requires the plan edges to match the
graph byte-for-byte, then visits only the named consumer checkouts.

Each selected repository owns a
`family-verification-checks-v1.json` surface map.  The current `declaration`
runner proves the exact consumer declaration, its declared evidence digest,
and its command-free check identity.  Commons carries the identity and
metadata; it does not carry arbitrary shell commands or execute repositories.
Actions writes one check result, execution receipt, and evidence manifest per
selected consumer, followed by `composite-proof.json`.  A composite PASS is
only selected-consumer proof, never family-wide proof.

An earlier composite proof can be supplied with `--prior-proof`.  Reuse
requires the same plan identity, graph identity, edge fingerprint, contract
revision, consumer declaration/evidence identities, and consumer repository
revision.  Otherwise the consumer check runs again.

```text
python scripts/selective_family_verify.py \
  --plan .mncs/verification-plan.json \
  --graph family/MNCS-Commons/family/semantic-edges-v1.json \
  --workspace-root family \
  --output-dir .mncs/selective-family-proof \
  [--prior-proof .mncs/previous-proof]
```
