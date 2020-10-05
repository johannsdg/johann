# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

PYPISERVER_FALLBACK_URL ?= https://pypi.org/simple/
DOCKER_COMPOSE = JOHANN_CORE_ROOT=./ docker-compose -p johann -f docker-compose.yml
DOCKER_COMPOSE_DEV = $(DOCKER_COMPOSE) -f docker-compose.dev.yml
DOCKER_COMPOSE_TEST = $(DOCKER_COMPOSE) -f docker-compose.test.yml
DOCKER_COMPOSE_ALL = $(DOCKER_COMPOSE) -f docker-compose.dev.yml -f docker-compose.test.yml
VENV_PATH = ./venv
VENV_PYTHON = $(VENV_PATH)/bin/python3
PRE_COMMIT = $(VENV_PATH)/bin/pre-commit
SAFETY = $(VENV_PATH)/bin/safety
TWINE = $(VENV_PATH)/bin/twine


# Building and running Johann
up: build
	$(DOCKER_COMPOSE) up -d

logs:
	$(DOCKER_COMPOSE_ALL) logs -f

build: prep
	$(DOCKER_COMPOSE) build

redis-happy:  # Optional - address Redis warnings
	sudo bash -c "echo never > /sys/kernel/mm/transparent_hugepage/enabled"
	sudo sysctl vm.overcommit_memory=1

prep:
	docker network inspect johann_public > /dev/null 2>&1 || docker network create johann_public
	docker network inspect johann_servicenet > /dev/null 2>&1 || docker network create --internal johann_servicenet
	if [ ! -f plugins.txt ]; then cp plugins.default.txt plugins.txt; fi
	mkdir -p local_packages
	$(DOCKER_COMPOSE) up -d pypiserver


# Cleanup
clean: kill
	$(DOCKER_COMPOSE_ALL) down --volumes --remove-orphans
	$(DOCKER_COMPOSE_ALL) rm -f
	docker network prune -f
	$(MAKE) clean-files
	# PLANNED: remove __pycache__ dirs

kill:
	$(DOCKER_COMPOSE_ALL) kill

clean-all: clean
	rm -rf local_packages
	rm -rf venv

clean-files:
	rm -f johann.*.log
	rm -f johann.*.pid
	rm -f johann/*.log
	rm -f johann/*.pid
	rm -f johann/requirements.txt
	rm -f johann/plugins.txt
	rm -rf build dist ./*.egg-info

kill-all:  # will kill any non-Johann containers also
	-docker kill `docker ps -q`


# Johann development - building and running
dev: dev-build
	@if [ ! -f $(PRE_COMMIT) ]; then $(MAKE) dev-setup; fi
	$(DOCKER_COMPOSE_ALL) up -d

dev-build: dev-prep
	$(MAKE) package
	cp dist/* ./local_packages/
	$(DOCKER_COMPOSE_DEV) build

dev-prep: prep
	# when mounting repo dir as docker volume, this becomes necessary
	cp requirements.txt johann/
	cp plugins.txt johann/


# Johann development - linting
lint:
	@if [ ! -f $(PRE_COMMIT) ]; then $(MAKE) dev-setup; fi
	$(PRE_COMMIT) run check-ast
	$(PRE_COMMIT) run --show-diff-on-failure

lint-all:
	@if [ ! -f $(PRE_COMMIT) ]; then $(MAKE) dev-setup; fi
	$(PRE_COMMIT) run -a check-ast
	$(PRE_COMMIT) run -a --show-diff-on-failure

safety:
	@if [ ! -f $(SAFETY) ]; then $(MAKE) dev-setup; fi
	$(SAFETY) check


# Johann development - testing
test: clean-all
	$(MAKE) test-build
	$(MAKE) test-up
	sleep 5
	$(MAKE) test-run

test-build: prep
	$(DOCKER_COMPOSE_TEST) build

test-up:
	$(DOCKER_COMPOSE_TEST) up -d

test-run:  # run tests without cleaning/rebuilding
	$(DOCKER_COMPOSE_TEST) run tester pytest -sv

tester:  # start a persistent test container with tests/ mounted
	$(DOCKER_COMPOSE_TEST) run -d tester tail -f /dev/null

test-containers:  # (re)create test target containers
	$(DOCKER_COMPOSE_TEST) up -d --force-recreate blank_3.6_buster blank_3.7_buster


# Johann development - other
dev-setup: dev-venv
	$(PRE_COMMIT) install --install-hooks -t pre-commit -t commit-msg -t pre-push

requirements:
	@if [ ! -f $(PRE_COMMIT) ]; then $(MAKE) dev-setup; fi
	-$(PRE_COMMIT) run -a --show-diff-on-failure pip-compile

pypiserver: prep
	# prep starts pypiserver for us

dev-venv:
	python3 -m venv $(VENV_PATH)
	$(VENV_PYTHON) -m pip install 'wheel>=0.33.6'
	$(VENV_PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt

package:
	@if [ ! -f $(TWINE) ]; then $(MAKE) dev-setup; fi
	$(VENV_PYTHON) setup.py sdist bdist_wheel
	$(TWINE) check dist/*

.PHONY: venv build test logs dev prep clean lint safety requirements
