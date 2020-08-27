# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

.PHONY: venv build test logs dev prep clean lint safety requirements

PROJECT_NAME = johann
DOCKER_COMPOSE = JOHANN_CORE_ROOT=. docker-compose -p $(PROJECT_NAME)
COMPOSE_FILES_ALL = -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.test.yml
COMPOSE_FILES_TEST = -f docker-compose.yml -f docker-compose.test.yml
VENV_PATH = ./venv
PIP_COMPILE = pip-compile --quiet --allow-unsafe --generate-hashes --no-emit-index-url
PRE_COMMIT = $(VENV_PATH)/bin/pre-commit
SAFETY = $(VENV_PATH)/bin/safety


# Running Johann
up: build
	$(DOCKER_COMPOSE) up -d

logs:
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_ALL) logs -f


# Cleanup
clean: kill
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_ALL) down --volumes --remove-orphans
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_ALL) rm -f
	docker network prune -f
	$(MAKE) clean-files

clean-files:
	rm -f $(PROJECT_NAME)/*.log
	rm -f $(PROJECT_NAME)/*.pid
	rm -f $(PROJECT_NAME)/requirements_plugins.txt
	rm -f $(PROJECT_NAME)/requirements_base.txt

kill:
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_ALL) kill

kill-all:  # will kill any non-Johann containers also
	-docker kill `docker ps -q`

# Johann development - running
dev: build
	@if [ ! -f $(PRE_COMMIT) ]; then $(MAKE) dev-setup; fi
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_ALL) up -d


# Johann development - linting
lint:
	@if [ ! -f $(PRE_COMMIT) ]; then $(MAKE) dev-setup; fi
	$(PRE_COMMIT) run --show-diff-on-failure

safety:
	@if [ ! -f $(SAFETY) ]; then $(MAKE) dev-setup; fi
	$(SAFETY) check


# Johann development - testing
test: clean
	$(MAKE) test-build
	$(MAKE) test-up
	sleep 5
	$(MAKE) test-run

test-build: prep
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_TEST) build

test-up:
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_TEST) up -d

test-run:  # run tests without cleaning/rebuilding
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_TEST) run tester pytest -sv

tester:  # start a persistent test container with tests/ mounted
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_TEST) run -d tester tail -f /dev/null

test-containers:  # (re)create test target containers
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_TEST) up -d --force-recreate blank_3.6_buster blank_3.7_buster


# Johann development - other
dev-setup: dev-venv
	$(PRE_COMMIT) install --install-hooks

requirements:
	-$(PRE_COMMIT) run -a pip-compile

dev-venv:
	python3 -m venv $(VENV_PATH)
	$(VENV_PATH)/bin/python3 -m pip install 'wheel>=0.33.6'
	$(VENV_PATH)/bin/python3 -m pip install -r requirements-dev.txt


# Building Johann
build: prep
	$(DOCKER_COMPOSE) $(COMPOSE_FILES_ALL) build

redis-happy:  # Optional - address Redis warnings
	sudo bash -c "echo never > /sys/kernel/mm/transparent_hugepage/enabled"
	sudo sysctl vm.overcommit_memory=1

prep:
	docker network inspect johann_public > /dev/null 2>&1 || docker network create johann_public
	docker network inspect johann_servicenet > /dev/null 2>&1 || docker network create --internal johann_servicenet

	# gather plugin requirements
	if bash -c "compgen -G './plugins/*/requirements.txt' >/dev/null"; then \
		cat ./plugins/*/requirements.txt > $(PROJECT_NAME)/requirements_plugins.txt; \
	else \
		touch $(PROJECT_NAME)/requirements_plugins.txt; \
	fi

	# copy base requirements
	cp ./requirements.txt $(PROJECT_NAME)/requirements_base.txt
