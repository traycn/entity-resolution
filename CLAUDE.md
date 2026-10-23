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
- `dup_type` values: `original`, `dup`, `hard_dup`, `drift_chain` (M5), and `household_same_name`: ~2% of household members are the host's same-name child (Jr./Sr.: same first and last name, the host's phone or email, DOB 18–40 years later). The child has exactly one record and is a different entity, so every pair with the parent is a non-duplicate that must not MATCH.
- **Accept:** same seed → identical output; truth file covers every record.

### M2 — Normalizers
- Implement every function in `normalize.py` (names, email, phone → E.164, address, dates).
- **Accept:** table-driven tests for each normalizer, ≥ 15 cases each, including Unicode names.
- Known assumption: slash/dash dates are parsed month-first (US); two-digit years use a fixed pivot (>30 → 19xx).

### M3 — Blocking
- Generate candidate pairs using multiple blocking keys (email local-part, phone last 7, name soundex + zip). Union the blocks.
- **Accept:** report *pair completeness* (share of true duplicate pairs that survive blocking) ≥ 0.98 and reduction ratio ≥ 0.999 on 50k records.

### M4 — Scoring and decisions
- Weighted field similarity (rapidfuzz) + hard rules. Output one of `MATCH`, `REVIEW`, `NO_MATCH` with thresholds in `src/resolver/config.toml`.
- **Accept:** safety-rule test passes; every decision has an explanation dict.
- **Auto-merge policy** (`src/resolver/config.toml` `[policy]`): `MATCH` only on an exact normalized email or phone match whose first names agree (Jaro-Winkler ≥ `min_first_name_similarity` = 0.80, nickname-normalized, swapped first/last allowed); below that → `REVIEW`, `rule="shared_identifier"`. Then two gates:
  - First names similar but **not identical** after normalization, and the *other* identifier on both records and different (same phone, different emails, or vice versa) → `REVIEW`, `rule="exact_id_other_mismatch"`. Why: a household phone next to two different emails is two people (Dustin/Destiny Smith, 0.86).
  - First names **identical**, both DOBs present and different → `REVIEW`, `rule="exact_id_dob_mismatch"`. Why: a same-name parent and child (Jr./Sr.) share a household identifier. Identical names with a differing other identifier still MATCH: that is usually one person's new phone or email, and sending them all to REVIEW cost ~15 points of ordinary recall.
  - Known gap: identical names with a DOB missing on either side MATCH even if the other identifier differs, so a Jr./Sr. pair can still merge that way. A guard for it (`refined_dob_guard` in `eval/compare_policies.py`) was measured and not adopted: it must cost < 0.5 points of mean ordinary recall over the six seeds and costs far more. Numbers: `docs/results.md`, "Exact-ID policy comparison" and "Known limitations" (`make compare`). The weighted score never auto-merges (`allow_weighted_match = false`); it only picks `REVIEW` vs `NO_MATCH`. Why: name + address can't tell a person from a same-named family member, and a look-alike email is a different identifier.
  - Numbers (decision counts, hard_dup / household merges, and the `min_first_name_similarity` sweep on seeds 42, 7 and 1234): see `docs/results.md`, regenerated by `make eval`. Don't copy numbers into this file.

### M5 — Clustering and merge
- Connected components on MATCH edges, but split any cluster that contains a conflicting-identifier pair, or (v1.1.0) a DOB-mismatch pair: identical first names, both DOBs present and different (`merge.is_dob_mismatch`). Why: a record with no DOB can exact-match both a parent and a same-name child and bridge them (found by project 02). A reviewer approving that exact pair lifts the DOB cannot-link; nothing lifts the conflicting-identifier rule. Survivorship rules: most recent non-null, prefer verified source.
- **Accept:** golden-record output + an audit log of every merge (reversible).
- **Survivorship** (`merge.build_golden`):
  - Verified beats recency: records rank by `email_verified`, then most recent `updated_at`, then record id. The verified flag is the only verification signal in the sources, so it ranks the whole record, not just the email.
  - Field groups stay together: name (first + last), email (+ verified flag), phone, address (street + city + state + zip), dob are each taken whole from one record, so a golden address never mixes one record's street with another's zip. Each field records its source `record_id`.
  - Email is stored as written (the normalizer only validates it). The normalized form strips gmail dots/plus tags; it is a matching key, not the customer's address. Phone is stored as E.164 and dob as ISO. A value its normalizer rejects counts as missing.
- Identity drift: the generator gives ~2% of entities a 3-record `drift_chain` (phone changes 1→2, email changes 2→3), so records 1 and 3 conflict and are linked only through record 2. The conflict check must split these; no cluster may contain more than one true entity.

### M6 — Evaluation
- `eval/evaluate.py` prints pairwise and cluster-level precision/recall/F1, plus a threshold sweep chart (PNG).
- **Accept:** precision ≥ 0.99 at the chosen threshold. Numbers go into README Result.
- Report `hard_dup` pairs separately. `truth.csv` has a `dup_type` column (`original` / `dup` / `hard_dup`); ~2% of duplicates are `hard_dup`, i.e. they differ on BOTH email and phone. The safety rule is expected to leave them `NO_MATCH` or `REVIEW`, so they are the known recall cost of the safety rule, not a bug. Print recall with and without them (e.g. "recall on ordinary duplicates" vs "hard_dup pairs found / total") and never count a merge of them as a win.

### M7 — Review UI
- FastAPI endpoint serving REVIEW pairs; Vite + React + Tailwind page showing side-by-side records with field-level scores and Approve / Reject buttons. Decisions write to `data/review_decisions.csv`.
- Clusters split by the conflict check (`out/splits.jsonl`) must appear in the review queue with the evidence that linked them: the component's members, the MATCH edges with their explanations (per-field scores, rule), the conflicting pairs, and the edges the split rejected. A reviewer must be able to see why the records were connected and why they were split.
- Applying decisions (`make apply-review` → `out/reviewed/`): approved pairs become `human_approved` links that win over automatic edges; rejected pairs become cannot-link constraints; stale decisions (item no longer in the queue) are skipped. The safety rule outranks reviewers: a split marked "should be one entity" is refused (`refused_safety_rule`, or `refused_dob_rule` for a split made only by a birth-date mismatch: approve the REVIEW pair instead). `out/` stays the automatic run that eval and CI measure.
- **Accept:** works locally with `make review`.

### M8 — Polish
- GitHub Actions CI, `docs/architecture.md` (Mermaid), README filled in.

## Definition of done
`make all` regenerates data, resolves, evaluates, and prints the metrics table from a clean clone.
