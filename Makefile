.PHONY: data resolve eval compare merge-report blocking-report review test lint all

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

merge-report:
	uv run python eval/merge_report.py --out out --truth data/truth.csv

blocking-report:
	uv run python eval/blocking_report.py

review:
	uv run uvicorn resolver.review_api:app --reload & cd review-ui && npm run dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy --strict src eval

all: data resolve eval compare
