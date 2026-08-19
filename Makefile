# PostHog Engineering Impact Dashboard
#
#   Phase 1  ingest -> normalize -> graph -> features -> validate -> export
#   Phase 2  verify-inputs -> graph -> episodes -> attribute -> analytics ->
#            dimensions -> portfolios -> rank -> validate -> export
#
# Every target is independently rerunnable and resumable.  Nothing here builds
# or executes the analysed repository; it is cloned read-only.

PY      ?= ./.venv/bin/python
PIP     ?= ./.venv/bin/pip
PYTEST  ?= ./.venv/bin/pytest
WORKERS ?= 4

# The pipeline is imported from src/ rather than installed, so every recipe that
# runs `python -m impact` needs src on the path. pytest gets this from
# pyproject.toml; make has to say it.
export PYTHONPATH := src$(if $(PYTHONPATH),:$(PYTHONPATH),)

.PHONY: help venv deps all ingest ingest-git ingest-github normalize graph \
        features validate export test clean-artifacts ingest-web \
        p2 p2-verify p2-graph p2-episodes p2-attribute p2-analytics \
        p2-dimensions p2-portfolios p2-rank p2-llm p2-validate p2-export \
        p2-queue p2-queue-status p2-fixtures \
        p2-test status

help:
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv:                     ## create the virtualenv
	python3.12 -m venv .venv

deps: venv                ## install pinned dependencies
	$(PIP) install -r requirements.txt

# ---------------------------------------------------------------- phase 1 ---

all: ingest normalize ingest-web graph features validate export  ## full phase 1 pipeline

ingest: ingest-git ingest-github

ingest-git:               ## clone/verify + commits + diffs
	$(PY) -m impact ingest-git

ingest-github:            ## discovery + PR core + review detail (network-bound)
	$(PY) -m impact ingest-github --workers $(WORKERS)

normalize:                ## raw -> normalized entity tables
	$(PY) -m impact normalize

ingest-web:               ## fetch docs/changelog pages referenced from PRs
	$(PY) -m impact ingest-web

graph:                    ## language-aware module dependency graph
	$(PY) -m impact graph

features:                 ## deterministic evidence features
	$(PY) -m impact features

validate:                 ## invariants, reconciliation, quality gates
	$(PY) -m impact validate

export:                   ## artifacts/ package + run manifest
	$(PY) -m impact export

# ---------------------------------------------------------------- phase 2 ---

p2: p2-verify p2-graph p2-episodes p2-attribute p2-analytics p2-dimensions \
    p2-portfolios p2-rank p2-validate p2-export   ## full phase 2 pipeline

p2-verify:                ## verify phase 1 manifest + table hashes
	$(PY) -m impact2 verify-inputs

p2-graph:                 ## tiered artifact graph (A/B/C edges)
	$(PY) -m impact2 graph

p2-episodes:              ## episode construction + clustering audit
	$(PY) -m impact2 episodes

p2-attribute:             ## role-aware participants and shared credit
	$(PY) -m impact2 attribute

p2-analytics:             ## propagation, decay, novelty, corrective, causality
	$(PY) -m impact2 analytics

p2-dimensions:            ## six evidence-banded impact dimensions
	$(PY) -m impact2 dimensions

p2-portfolios:            ## per-engineer portfolios (OWA aggregation)
	$(PY) -m impact2 portfolios

p2-rank:                  ## ELECTRE III outranking across scenarios
	$(PY) -m impact2 rank

p2-llm:                   ## optional LLM semantic layer (cached, replayable)
	$(PY) -m impact2 llm

p2-validate:              ## the 10-item validation program
	$(PY) -m impact2 validate

p2-export:                ## static Phase 3 package
	$(PY) -m impact2 export

p2-queue:                 ## wait for the ingest to finish, then rebuild everything
	@nohup ./scripts/run_when_ingest_completes.sh >/dev/null 2>&1 & \
	  echo "queued (pid $$!). Progress: reports/phase2/auto_run_status.json"

p2-queue-status:          ## where the queued run has got to
	@cat reports/phase2/auto_run_status.json 2>/dev/null || echo "no queued run"

p2-fixtures:              ## regenerate the Phase 3 fixture package
	$(PY) scripts/make_phase3_fixtures.py

# ------------------------------------------------------------------ tests ---

test:                     ## phase 1 unit + contract tests (no network)
	$(PYTEST) tests -m 'not integration'

p2-test:                  ## phase 2 tests
	$(PYTEST) tests/phase2 -m 'not integration'

status:                   ## how far the pipeline has got
	@echo "raw pr_core shards : $$(ls data/raw/github/pr_core 2>/dev/null | wc -l)"
	@echo "raw pr_detail      : $$(ls data/raw/github/pr_detail 2>/dev/null | wc -l)"
	@echo "normalized tables  : $$(ls data/normalized/*.parquet 2>/dev/null | wc -l)"
	@echo "derived tables     : $$(ls data/derived/*.parquet 2>/dev/null | wc -l)"
	@echo "phase1 artifacts   : $$(ls artifacts/*.parquet 2>/dev/null | wc -l)"
	@echo "phase2 tables      : $$(ls data/phase2/*.parquet 2>/dev/null | wc -l)"
	@echo "phase3 export      : $$(ls artifacts/phase3/*.json 2>/dev/null | wc -l)"

clean-artifacts:          ## remove generated artifacts (keeps raw + clone)
	rm -rf artifacts schemas reports data/normalized data/derived/*.parquet \
	       data/phase2
