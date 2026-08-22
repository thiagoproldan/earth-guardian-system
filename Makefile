# Earth Guardian - development tasks.

PY      ?= python
PYTEST  ?= $(PY) -m pytest
RUFF    ?= ruff
export PYTHONPATH := .

.DEFAULT_GOAL := help
.PHONY: help demo simulate analyze plan impact dashboard gif test lint format check clean distclean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

demo:  ## Full pipeline: simulate -> ingest -> GAIA -> plan -> impact
	$(PY) -m earthguardian.cli demo

simulate:  ## Generate a season and curate it
	$(PY) -m earthguardian.cli simulate --days 365

analyze:  ## Run GAIA over the curated uplinks
	$(PY) -m earthguardian.cli analyze

plan:  ## The irrigation decision per plot
	$(PY) -m earthguardian.cli plan

impact:  ## Rainfed vs calendar vs sensor-driven
	$(PY) -m earthguardian.cli impact

dashboard:  ## Launch the console
	$(PY) -m earthguardian.cli dashboard

gif:  ## Re-capture the console imagery (needs `nix develop .#media`)
	$(PY) scripts/capture_console.py

test:  ## Run the test-suite
	$(PYTEST)

lint:  ## Lint and check formatting
	$(RUFF) check .
	$(RUFF) format --check .

format:  ## Apply the formatter
	$(RUFF) format .
	$(RUFF) check --fix .

check: lint test  ## Everything CI runs

clean:  ## Remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov

distclean: clean  ## Also remove generated data
	rm -rf data/raw data/curated outputs
