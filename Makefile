SHELL := /bin/bash
ARK_HOME := $(CURDIR)
PY := $(ARK_HOME)/.venv/bin/python
PATH := $(ARK_HOME)/bin/node/bin:$(PATH)

export ARK_HOME
export PIP_CACHE_DIR := $(ARK_HOME)/data/cache/pip
export npm_config_cache := $(ARK_HOME)/data/cache/npm
export PLAYWRIGHT_BROWSERS_PATH := $(ARK_HOME)/data/cache/ms-playwright

.PHONY: all test lint typecheck unit frontend e2e check-no-ext dev logs doctor restart install fmt

all: test

# The full gate run in this container and in CI.
test: lint unit frontend typecheck

lint:
	$(PY) -m ruff check ark tests scripts
	$(PY) -m ruff format --check ark tests scripts

ark/frontend/node_modules:
	cd ark/frontend && npm ci

typecheck: ark/frontend/node_modules
	$(PY) -m mypy ark
	cd ark/frontend && npm run typecheck

unit:
	$(PY) -m pytest -q tests --ignore=tests/e2e

frontend: ark/frontend/node_modules
	cd ark/frontend && npm run build
	$(PY) scripts/check_no_external_urls.py ark/frontend/dist

e2e:
	$(PY) -m pytest -q tests/e2e

check-no-ext:
	@test -d ark/frontend/dist && $(PY) scripts/check_no_external_urls.py ark/frontend/dist || echo "dist not built yet — skipping no-ext check"

dev:
	./scripts/dev.sh

logs:
	tail -F data/logs/ark.log

doctor:
	$(PY) -m ark doctor

restart:
	sudo systemctl restart ark

install:
	sudo bash scripts/install.sh

fmt:
	$(PY) -m ruff format ark tests scripts