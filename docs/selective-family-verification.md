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
only selected-consumer proof, never family-wide proof. On the normal command
line path, Actions invokes its native application module and treats the
returned family result, receipt, and selected proof as authoritative. Python
remains a bounded document/filesystem adapter. When adapting selected Test
receipts into the native provider request, it admits the receipt against the
provider descriptor and artifact-bound callable binding metadata, then uses
`mncs-embed`'s generic structured projection for the declared `ProviderRequest`
contract. The compiler artifact owns nominal identity, primitive widths,
finite variants, nested value rules, and bounds. The explicit receipt-to-field
mapping remains part of the Actions trust/admission boundary. The
`--python-compatibility-oracle` path is reserved for explicit comparison tests.

The composite proof also binds the producer repository, its declaration
revision, producer declaration identity, producer evidence digests, and the
producer checkout revision when that checkout is available.  Source-change
content identity remains separate from repository revision.  Source archives
and synthetic fixtures use a conservative `manifest:<identity>` revision
instead of pretending to have a VCS revision.

An earlier composite proof can be supplied with `--prior-proof`.  Reuse
requires the same plan identity, graph identity, edge fingerprint, contract
revision, consumer declaration/evidence identities, and consumer repository
revision, plus the producer repository revision.  Otherwise the consumer check
runs again.

```text
python scripts/selective_family_verify.py \
  --plan .mncs/verification-plan.json \
  --graph family/MNCS-Commons/family/semantic-edges-v1.json \
  --workspace-root family \
  --output-dir .mncs/selective-family-proof \
  --native-actions-source native/mncs/actions/family.mncs \
  [--prior-proof .mncs/previous-proof]
```
