# CLAUDE.md — Entity Resolution Toolkit

## Goal
A Python library + CLI that takes customer records from several synthetic "source systems," normalizes them, finds duplicates, and merges only when confident. Borderline pairs go to a human review queue. The repo must *measure* its own accuracy against labeled ground truth.

This is the public twin of the portfolio claim: "~20,000 duplicate records resolved, with validation safeguards to prevent incorrect merges."

## Constraints
- Python 3.12, `uv` for dependencies, `ruff` + `mypy --strict` + `pytest`.
- Allowed libs: `pandas` or `polars`, `rapidfuzz`, `phonenumbers`, `email-validator`, `faker`, `typer`, `fastapi` (review API only). No paid services.
- Every merge decision must be explainable: store per-field scores and the rule that fired.
- Never merge two records that have conflicting hard identifiers (e.g., different verified emails AND different phones) — this is a safety rule with its own test.

## Repo layout (already stubbed)
```
src/resolver/normalize.py   # field normalizers
src/resolver/blocking.py    # candidate-pair generation
src/resolver/scoring.py     # pairwise similarity + rules
src/resolver/merge.py       # clustering + survivorship
src/resolver/cli.py         # typer CLI
data/generate.py            # synthetic data with known duplicates
eval/evaluate.py            # precision / recall / F1 vs ground truth
review-ui/                  # small React (Vite) app for borderline pairs
tests/
```

## Milestones

### M1 — Synthetic data generator
- `data/generate.py --n 50000 --dup-rate 0.25 --seed 42` writes `data/sources/{crm,ads,web}.csv` and `data/truth.csv` (`record_id, entity_id, dup_type`).
- Duplicates are corrupted realistically: typos, nicknames (Bob/Robert), swapped first/last, phone formats, `gmail` dot/plus variants, address abbreviations (St/Street), missing fields.
- **Accept:** same seed → identical output; truth file covers every record.

### M2 — Normalizers
- Implement every function in `normalize.py` (names, email, phone → E.164, address, dates).
- **Accept:** table-driven tests for each normalizer, ≥ 15 cases each, including Unicode names.
- Known assumption: slash/dash dates are parsed month-first (US); two-digit years use a fixed pivot (>30 → 19xx).

### M3 — Blocking
- Generate candidate pairs using multiple blocking keys (email local-part, phone last 7, name soundex + zip). Union the blocks.
- **Accept:** report *pair completeness* (share of true duplicate pairs that survive blocking) ≥ 0.98 and reduction ratio ≥ 0.999 on 50k records.

### M4 — Scoring and decisions
- Weighted field similarity (rapidfuzz) + hard rules. Output one of `MATCH`, `REVIEW`, `NO_MATCH` with thresholds in `config.toml`.
- **Accept:** safety-rule test passes; every decision has an explanation dict.

### M5 — Clustering and merge
- Connected components on MATCH edges, but split any cluster that contains a conflicting-identifier pair. Survivorship rules: most recent non-null, prefer verified source.
- **Accept:** golden-record output + an audit log of every merge (reversible).

### M6 — Evaluation
- `eval/evaluate.py` prints pairwise and cluster-level precision/recall/F1, plus a threshold sweep chart (PNG).
- **Accept:** precision ≥ 0.99 at the chosen threshold. Numbers go into README Result.
- Report `hard_dup` pairs separately. `truth.csv` has a `dup_type` column (`original` / `dup` / `hard_dup`); ~2% of duplicates are `hard_dup`, i.e. they differ on BOTH email and phone. The safety rule is expected to leave them `NO_MATCH` or `REVIEW`, so they are the known recall cost of the safety rule, not a bug. Print recall with and without them (e.g. "recall on ordinary duplicates" vs "hard_dup pairs found / total") and never count a merge of them as a win.

### M7 — Review UI
- FastAPI endpoint serving REVIEW pairs; Vite + React + Tailwind page showing side-by-side records with field-level scores and Approve / Reject buttons. Decisions write to `data/review_decisions.csv`.
- **Accept:** works locally with `make review`.

### M8 — Polish
- GitHub Actions CI, `docs/architecture.md` (Mermaid), README filled in.

## Definition of done
`make all` regenerates data, resolves, evaluates, and prints the metrics table from a clean clone.
