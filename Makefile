.PHONY: data resolve eval blocking-report review test lint all

data:
	uv run python data/generate.py --n 50000 --dup-rate 0.25 --seed 42

resolve:
	uv run resolver run --sources data/sources --out out/

# Until M6 adds eval/evaluate.py (pairwise/cluster P/R/F1), eval reports on the
# merge outputs written by `make resolve`.
eval:
	uv run python eval/merge_report.py --out out --truth data/truth.csv

blocking-report:
	uv run python eval/blocking_report.py

review:
	uv run uvicorn resolver.review_api:app --reload & cd review-ui && npm run dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy --strict src

all: data resolve eval
