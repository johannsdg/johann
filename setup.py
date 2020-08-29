# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="johann",
    version="0.1.0-alpha",
    description="Lightweight and flexible scenario orchestration",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Jeffrey James",
    author_email="lobotmcj@gmail.com",
    license="BSD",
    url="https://github.com/johannsdg/johann",
    packages=["johann"],
    include_package_data=True,
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Operating System :: POSIX :: Linux",
    ],
    python_requires=">=3.6, <4",
    install_requires=[
        "aiohttp>=3.6,<4.0",
        "celery==4.4.6",  # 4.4.7 breaks imports somehow
        "dataclasses<0.7",
        "docker>=4.1,<5.0",
        "fastapi>=0.56.0,<1.0",
        "logzero>=1.5,<2.0",
        "marshmallow>=3.2,<4.0",
        "marshmallow_enum>=1.5.0,<2.0",
        "natsort>=6.0.0,<7.0",
        "psutil>=5.6,<6.0",
        "python-dotenv>=0.10.1,<0.11.0",
        "redis>=3.2,<4.0",
        "requests>=2.24.0,<3.0",
        "ruamel.yaml>0.15.0,<0.16",
    ],
)
