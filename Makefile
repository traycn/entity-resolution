.PHONY: data resolve eval compare apply-review merge-report blocking-report review test lint all

data:
	uv run python data/generate.py --n 50000 --dup-rate 0.25 --seed 42

resolve:
	uv run resolver run --sources data/sources --out out/

# Scores out/ against data/truth.csv, re-runs seeds 7 and 1234 plus held-out seeds
# 2026, 99 and 31337 in-process, writes docs/results.md + docs/threshold_sweep.png
# and rewrites the README Result table. Needs `make data resolve`.
eval:
	uv run python eval/evaluate.py --out out --sources data/sources --truth data/truth.csv --seed 42 --readme README.md

# Original / strict / refined / refined + missing-DOB guard exact-ID policies on
# six seeds; fills the policy comparison and DOB-gap blocks of docs/results.md.
compare:
	uv run python eval/compare_policies.py --readme README.md

# Apply the review decisions (data/review_decisions.csv) on top of the automatic
# run: approved pairs merge, rejected pairs stay apart, the safety rule still wins.
# Writes out/reviewed/ (incl. decisions_applied.jsonl); out/ stays automatic.
apply-review:
	uv run resolver run --sources data/sources --out out/reviewed --decisions data/review_decisions.csv

merge-report:
	uv run python eval/merge_report.py --out out --truth data/truth.csv

blocking-report:
	uv run python eval/blocking_report.py

# Review UI: API on 127.0.0.1:8000, page on http://127.0.0.1:5173 (proxies /api).
# Builds data + out/ if missing; Ctrl-C stops both servers.
review:
	@test -f out/review.jsonl || $(MAKE) data resolve
	@test -d review-ui/node_modules || (cd review-ui && npm ci --no-audit --no-fund)
	@trap 'kill 0' INT TERM EXIT; \
	uv run uvicorn resolver.review_api:app --host 127.0.0.1 --port 8000 & \
	cd review-ui && npm run dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy --strict src eval

all: data resolve eval compare
