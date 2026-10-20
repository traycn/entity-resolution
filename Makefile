.PHONY: data resolve eval review test lint all

data:
	uv run python data/generate.py --n 50000 --dup-rate 0.25 --seed 42

resolve:
	uv run resolver run --sources data/sources --out out/

eval:
	uv run python eval/evaluate.py --pred out/clusters.csv --truth data/truth.csv

review:
	uv run uvicorn resolver.review_api:app --reload & cd review-ui && npm run dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy --strict src

all: data resolve eval
