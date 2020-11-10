![Latest release](https://img.shields.io/github/v/release/johannsdg/johann?include_prereleases&sort=semver)
![Platform support](https://img.shields.io/badge/platform-linux-blue)
![Python version support](https://img.shields.io/badge/python-3.6%20%7C%203.7-blue)
[![License](https://img.shields.io/github/license/johannsdg/johann)](LICENSE)
[![pipeline status](https://gitlab.com/johannsdg/johann/badges/master/pipeline.svg)](https://gitlab.com/johannsdg/johann/-/commits/master)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

# Johann

Johann is a lightweight and flexible “scenario orchestrator”. It makes it easy to
coordinate the actions of groups of computers into cohesive, reconfigurable scenarios.
It’s sort of like the conductor for an orchestra of computers, and _you_ get to write
the music.

## Contents

- [Getting Started](#getting-started)
- [Usage](#usage)
- [This is an Alpha Prototype](#this-is-an-alpha-prototype)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Built With](#built-with)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [More Info](#more-info)

## Getting Started

First use git to clone [Johann](https://github.com/johannsdg/johann) and change
directories into the newly cloned repo.

### Installing

Johann is designed to be used on Linux and run in docker containers. It has been tested
on Ubuntu 18.04, and likely works on several other distributions as well.

Johann requires the following to run:

- [Docker Engine](https://docs.docker.com/engine/install/#server)
- [Docker Compose](https://docs.docker.com/compose/install/)
- Make

Here is an example of how to install these on Ubuntu/Debian:

```bash
# Install make
sudo apt-get update
sudo apt-get install build-essential

# Install docker via convenience script (not for production environments)
curl https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# log out and log back in
docker run hello-world

# Install docker-compose
sudo curl -L "https://github.com/docker/compose/releases/download/1.26.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version
```

### Running

Johann uses make to handle building and deploying its docker images. This can take a
while the first time.

```bash
make dev
```

## Usage

Johann orchestrates scenarios through reconfigurable YAML files called "Scores". Similar
to musical scores, these files describe the the actions that the "Players" must take to
perform their part/role in the scenario. Each Player's part in the scenario consists of
"Measures" -- specific tasks and timing that Players must perform. Again similar to
musical scores, the score file weaves these Measures together to orchestrate the Players
in the full scenario. Each Player is currently comprised of a group of Docker
container(s). In the future, these could be VMs or physical machines as well.

### Lexicon

- Score -> Scenario/script describing the actions that Players must take and when
- Player -> Group of Docker container(s) that play the same part/role in the scenario
- Host -> An actual compute resource such as a Docker container, VM, or physical
  machine.
- Measure -> Maps a Task and its timing/configuration to Player(s) in a Score
- Task -> Specific action taken by Player(s) as part of a Measure

### Example Score

The following is an example Score consisting of one Player ("docker_targets") with two
Hosts which are both Docker containers (`blank_3.6_buster` and `blank_3.7_buster`). Note
that the specific Hosts need not be specified in the Score file, and can be provided (or
changed) at runtime either via API or GUI. Both of these specific Hosts are stock Debian
containers with Python installed -- versions 3.6 and 3.7, respectively Neither of these
containers have Johann installed -- it will installed or updated ("pushed") at runtime
to match the version of Johann installed on the machine running Docker. This example
Score has just one Measure executed by one Player (2 Hosts), with a Task to run the
`ls -la` command in the root directory of each Host/container.

```yaml
# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

---
name: test_johann_pushing
category: testing
description:
  test Johann's push functionality to install the player software on a host that doesn't
  have it already
players:
  docker_targets:
    name: docker_targets
    hosts:
      - blank_3.6_buster
      - blank_3.7_buster
    image: None
    scale: 2
measures:
  - name: ls_root
    players: [docker_targets]
    start_delay: 0
    task: johann.tasks_main.run_shell_command
    args:
      - "ls -la /"
```

- The name `ls_root` is the arbitrary name of the Measure and is used as a key in the
  API to interact with the Measure.
- The `players` key specifies which Players from the list defined above should perform
  this particular Measure.
- The Task `johann.tasks_main.run_shell_command` specifies a specific compatible action
  from the Johann tasks. See
  [Johann's task code](https://github.com/johannsdg/johann/blob/master/johann/tasks_main.py)
  for a partial list of compatible Tasks.
  - Tasks are Python functions with the decorator `@celery_app.task`.
- The argument `ls -la` is supplied to the Task in this case to specify the command to
  be run as a shell command.

### Running a Score

With the Johann Docker containers running via `make dev`, users can interact with Scores
via either the command line or the web UI.

#### GUI

- Open a web browser and navigate to `http://127.0.0.1/`
- Click on the **Scenarios** tab to view the available Scores

- In the row containing the Score that you want to run, select one of the following
  options:

  - View: Displays the YAML and JSON representations of the Score file
  - Status: Displays the status of the current or last run depending on if a run in is
    progress
  - Launch: Runs the Score file
  - Reset: Resets the run to allow for a new run to be launched and monitored with
    status

- To run a Score select launch to be presented with the **Launch Scenario** screen.
- From this menu you can map available Hosts to the Players defined in the Score file.

  - Hosts can be added either via API or a file that is run at startup.

- Press the **Launch Scenario** button to launch the Score using the selected Hosts.

- This will automatically take you to the status page for the Score you just launched
  where you can watch the Measures of the Score play. **Note**: Some Scores, including
  the test score, may take a few minutes to initialize before running. This is where
  Johann is installing or updating itself on the Hosts.

* The status page also contains the raw output of the Tasks, in this case the `ls -la`
  command run on each container shown below.

#### Command Line

- To view available API endpoints

```sh
curl http://127.0.0.1:5000/
```

- To view available scores

```sh
curl http://127.0.0.1:5000/scores/
```

- To run a specific score

```sh
curl http://127.0.0.1:5000/affrettando/<score_name>
```

- To view the current status of a running score

```sh
watch 'curl http://127.0.0.1:5000/scores/<score_name>/status_short'
```

## This is an Alpha Prototype

Johann is an
[evolutionary prototype](https://en.wikipedia.org/wiki/Software_prototyping#Evolutionary_prototyping)
in its initial development. It is not yet feature complete, and breaking changes can
happen at any time. This is represented by its
[major version zero](https://semver.org/#spec-item-4) (0.y.z).

For now, Johann should be considered to be in perpetual
[alpha](https://en.wikipedia.org/wiki/Software_release_life_cycle#Alpha). This is made
explicit by the "-alpha" or "a" in the version identifier. Please expect it to be rough
around the edges (and maybe everywhere else).

Johann should only be used in isolated or protected networks of known and trusted
hosts/users. It should only be used for research and development, and **not** in
production.

## Roadmap

Here are some planned improvements to Johann, in no particular order:

- add more documentation
- switch to [pydantic](https://github.com/samuelcolvin/pydantic)
- switch to [fastapi](https://github.com/tiangolo/fastapi)
- use [mypy](https://github.com/python/mypy) and
  [pylint](https://github.com/pycqa/pylint)
- add more tests
- add user authentication
- support kwargs in Measures
- Score-level variables; configurable at runtime

## Contributing

We welcome pull requests! Before starting, please communicate with us to discuss what
you would like to change. Please also update tests and documentation as appropriate.
Thanks!

### Getting Started

Install development packages.

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install python3-dev python3-venv build-essential git

# Alpine (not officially supported)
apk add --no-cache python3-dev bash make git gcc linux-headers musl-dev
```

Setup the virtual environment used for Johann development. This also installs
[pre-commit](https://pre-commit.com/) hooks.

```bash
make dev-setup
```

### Development Usage

Start Johann in development mode (this will also build it).

```bash
make dev
```

### Testing

```bash
make test
```

### Linting

Johann uses [pre-commit](https://github.com/pre-commit/pre-commit). For the list of
hooks used, see [.pre-commit-config.yaml](.pre-commit-config.yaml).

Lint the files staged for commit.

```bash
make lint
```

Lint all files in the repo.

```bash
make lint-all
```

Use [safety](https://github.com/pyupio/safety) to check for known dependency
vulnerabilities.

```bash
make safety
```

### Contribution Flow

Johann uses a variant of GitHub Flow based on squash/rebase, which is most closely
described
[here](https://medium.com/singlestone/a-git-workflow-using-rebase-1b1210de83e5). If you
don't know how to squash or rebase yet, don't worry! We'll help you through it at PR
time!

- **Pull Requests should target the `develop` branch**
- commits must have the Developer Certificate of Origin (DCO) (`git commit -s`)
  - [DCO Wikipedia Article](https://en.wikipedia.org/wiki/Developer_Certificate_of_Origin)
- commits must also be PGP signed (`git commit -S`)
  - [Instructions from GitHub](https://docs.github.com/en/free-pro-team@latest/github/authenticating-to-github/managing-commit-signature-verification)
  - [Instructions from Git](https://git-scm.com/book/en/v2/Git-Tools-Signing-Your-Work)

_Again, if any of this is intimidating, don't worry about it! It was new to us too --
feel free to contact us or we can sort it out at PR time!_

### Pre-Commit Hooks

While they are opt-in, we strongly recommend the use of the pre-commit hooks provided
(see #Linting). These hooks will need to pass before a PR is accepted, and it helps to
work through them ahead of time. If you don't know how to or don't want to bother with a
particular hook failure(s), that's perfectly fine, and we will help you out at PR time
-- just prepend your git commit and/or push command with
`SKIP=[comma-separated list of hook names]` or, as a last resort, append `--no-verify`.

## Built With

In addition to the dependencies listed in [Requirements](#requirements), please see:

- [setup.py](setup.py)
- [requirements-dev.in](requirements-dev.in)
- [.pre-commit-config.yaml](.pre-commit-config.yaml)

In addition to these, Johann is also made possible with the help of (alphabetically):

- [gitlab-ci](https://about.gitlab.com/topics/ci-cd/)
- [pmtr](https://github.com/troydhanson/pmtr)
- [uwsgi-nginx-flask-docker](https://github.com/tiangolo/uwsgi-nginx-flask-docker)

## License

Use of this source code is governed by a BSD-3-clause license that can be found in the
[LICENSE](LICENSE) file. See the [AUTHORS](AUTHORS) file for names of contributors.

## Acknowledgments

- [JHU/APL](https://www.jhuapl.edu) for supporting Johann's licensing as open source
- Johann S.D.G.

## More Info

More information is available at the [docs](https://johannsdg.github.io/johann_docs/),
or you can contact someone from
[Johann S.D.G.](https://github.com/orgs/johannsdg/people) .
