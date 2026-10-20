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
_Fill from `make eval` output only._

| Metric | Value |
|---|---|
| Records processed | |
| Duplicates resolved | |
| Pairwise precision | |
| Pairwise recall | |
| Runtime (50k records) | |

## Run it
```bash
uv sync
make all      # generate → resolve → evaluate
make review   # start the review UI
```
