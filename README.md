# Entity Resolution Toolkit

> Live demo: _TBD_ · CI: _badge_

Find and safely merge duplicate customer records across systems — with every decision explained and measured.

## Problem
The same customer often exists as several records across CRM, ad platforms, and web forms because of inconsistent entry points and formatting. That fragments history and skews metrics. Naive dedupe makes it worse by merging people who aren't the same.

## Approach
- Normalize names, emails, phones (E.164), addresses and dates.
- Multi-key blocking to keep comparisons tractable.
- Weighted fuzzy scoring + hard safety rules → `MATCH` / `REVIEW` / `NO_MATCH`.
- Conflict-aware clustering, survivorship rules, reversible audit log.
- Human review UI for borderline pairs.
- Evaluation against labeled synthetic ground truth.

## Result
_Written by `make eval` and `make compare` between the markers below; do not edit by hand._

<!-- headline:start -->
**Pairwise precision 0.9998** (mean over 6 seeds, range 0.9996-1.0000) at **0.8698 recall on ordinary duplicates**, with 0 hard_dup merges. 10 of the 12 wrong-entity pairs across all seeds involve a same-name parent and child (Jr./Sr.); every wrong merge links different people through a shared phone or email. See [Known limitations](docs/results.md#known-limitations).
<!-- headline:end -->

### Auto-merge policy comparison
Same six seeds for every policy. The shipped policy is in bold; see [docs/results.md](docs/results.md#exact-id-policy-comparison) for per-seed numbers.

<!-- policies:start -->
Same data for every policy (seeds 42, 7, 1234, 2026, 99, 31337). Only pairs that reach the exact-ID branch with agreeing first names are re-decided. Shipped policy: **refined (name-aware + DOB mismatch)** (reproduces `score_pair` exactly).

Mean over all seeds (Jr./Sr. and mixed clusters summed):

| Policy | Precision | Ordinary recall | REVIEW queue | drift_chain merged | Mixed clusters | Jr./Sr. pairs MATCHed |
|---|---:|---:|---:|---:|---:|---:|
| original (no mismatch rule) | 0.9982 | 0.8876 | 1,662 | 739 / 2,250 | 92 | 118 / 118 |
| strict (any other-ID mismatch) | 0.9993 | 0.7379 | 4,990 | 0 / 2,250 | 28 | 30 / 118 |
| **refined (name-aware + DOB mismatch)** | 0.9998 | 0.8698 | 2,208 | 649 / 2,250 | 6 | 6 / 118 |
| refined + missing-DOB guard | 0.9999 | 0.8431 | 2,902 | 520 / 2,250 | 2 | 1 / 118 |
<!-- policies:end -->

### Seed 42 detail
<!-- results:start -->
Detail for seed 42, 50,000 synthetic records. Every seed, recall by duplicate kind, cluster-level metrics and the threshold sweep: [docs/results.md](docs/results.md).

| Metric | Value |
|---|---|
| Records processed | 50,000 |
| Duplicates resolved | 10,052 records merged away |
| Pairwise precision | 0.9996 (4 wrong-entity pairs; 0 of 238 hard_dup pairs merged) |
| Pairwise recall | 0.8659 on ordinary duplicates; 0.7644 on all true pairs |
| Pairs sent to human review | 2,226 |
| Runtime (50k records) | 4.8 s (block, normalize, score, cluster) |
<!-- results:end -->

## Run it
```bash
uv sync
make all      # generate → resolve → evaluate
make review   # start the review UI
```
