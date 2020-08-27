![Latest release](https://img.shields.io/github/v/release/johannsdg/johann?include_prereleases&sort=semver)
![Platform support](https://img.shields.io/badge/platform-linux-blue)
![Python version support](https://img.shields.io/badge/python-3.6%20%7C%203.7-blue)
![License](https://img.shields.io/github/license/johannsdg/johann)
![Gitlab pipeline status](https://img.shields.io/gitlab/pipeline/johannsdg/johann/master)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

# Johann

Johann is a lightweight and flexible “scenario orchestrator”. It makes it easy to
coordinate the actions of groups of computers into cohesive, reconfigurable scenarios.
It’s sort of like the conductor for an orchestra of computers, and _you_ get to write
the music.

## Summary

- [Requirements](#requirements)
- [Usage](#usage)
- [This is an alpha prototype](#this-is-an-alpha-prototype)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Built With](#built-with)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## Requirements

Johann is designed to be used on Linux. It has been tested on Ubuntu 18.04, and likely
works on several other distributions as well.

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

## Usage

Start Johann (this will also build it). This can take a while the first time.

```bash
make up
```

## This is an alpha prototype

Johann is an
[evolutionary prototype](https://en.wikipedia.org/wiki/Software_prototyping#Evolutionary_prototyping)
in its initial development. It is not yet feature complete, and breaking changes can
happen at any time. This is represented by its
[major version zero](https://semver.org/#spec-item-4) (0.y.z).

For now, Johann should be considered to be in perpetual
[alpha](https://en.wikipedia.org/wiki/Software_release_life_cycle#Alpha). This is made
explicit by the "-alpha" in the version identifier. Please expect it to be rough around
the edges (and maybe everywhere else).

Johann should only be used in isolated or protected networks of known and trusted
hosts/users. It should only be used for research and development, and never in
production.

## Roadmap

Here are some planned improvements to Johann, in no particular order:

- switch to [pydantic](https://github.com/samuelcolvin/pydantic)
- switch to [fastapi](https://github.com/tiangolo/fastapi)
- use [mypy](https://github.com/python/mypy)
- add more documentation
- add more tests
- add user authentication
- add a simple GUI

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
[pre-commit](https://pre-commit.com/).

```bash
make dev-setup
```

### Development Usage

Start Johann in development mode (this will also build it).

```bash
make dev
```

```bash
make requirements
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

Use [safety](https://github.com/pyupio/safety) to check for known dependency
vulnerabilities.

```bash
make safety
```

## Built With

In addition to the dependencies listed in [Requirements](#requirements) and
[requirements.in](requirements.in), Johann is made possible with the help of:

- [bandit](https://github.com/PyCQA/bandit)
- [black](https://github.com/psf/black)
- [flake8](https://gitlab.com/pycqa/flake8)
- [gitlab-ci](https://about.gitlab.com/topics/ci-cd/)
- [isort](https://github.com/timothycrosley/isort)
- [pip-tools](https://github.com/jazzband/pip-tools)
- [pmtr](https://github.com/troydhanson/pmtr)
- [pre-commit](https://github.com/pre-commit/pre-commit)
- [prettier](https://github.com/prettier/prettier)
- [pytest](https://github.com/pytest-dev/pytest)
- [safety](https://github.com/pyupio/safety)

## License

Use of this source code is governed by a BSD-3-clause license that can be found in the
LICENSE file. See the AUTHORS file for names of contributors.

## Acknowledgments

- [JHU/APL](https://www.jhuapl.edu) for supporting Johann's licensing as open source
- Johann S.D.G.
