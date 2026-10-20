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
_Written by `make eval` between the markers below; do not edit by hand._

<!-- results:start -->
Seed 42, 50,000 synthetic records. Other seeds (tuning and held-out), recall by duplicate kind, cluster-level metrics and the threshold sweep: [docs/results.md](docs/results.md).

| Metric | Value |
|---|---|
| Records processed | 50,000 |
| Duplicates resolved | 10,052 records merged away |
| Pairwise precision | 0.9996 (4 wrong-entity pairs; 0 of 238 hard_dup pairs merged) |
| Pairwise recall | 0.8659 on ordinary duplicates; 0.7644 on all true pairs |
| Pairs sent to human review | 2,226 |
| Runtime (50k records) | 4.5 s (block, normalize, score, cluster) |
<!-- results:end -->

## Run it
```bash
uv sync
make all      # generate → resolve → evaluate
make review   # start the review UI
```
