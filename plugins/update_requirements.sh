#!/bin/bash
#
# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

: "${UPGRADE:=false}"
: "${PIP_COMPILE_FLAGS:=--quiet --generate-hashes --allow-unsafe --no-emit-index-url}"
: "${PLUGINS:=`ls -d */`}"
echo_verb=Updat

if [ "${UPGRADE}" != false ]; then
    echo "Using --upgrade"
    PIP_COMPILE_FLAGS="${PIP_COMPILE_FLAGS} --upgrade"
    echo_verb=Upgrad
fi

if [ `command -v pip-compile >/dev/null` ]; then
    echo "This script requires pip-compile to work; please ensure that it is installed and on your PATH"
    exit 1
fi

count=0
for plugin in ${PLUGINS}
do
    reqs_in="${plugin}requirements.in"
    if [ ! -f ${reqs_in} ]; then
        echo "Skipping ${plugin} (no requirements.in)"
    elif ! grep -q '^\-c ../../requirements.txt' ${reqs_in}; then
        echo "Skipping ${plugin} ('requirements.in' is missing the requisite constraints file)"
    else
        echo "${echo_verb}ing ${plugin}requirements.txt ..."
        bash -c "cd ${plugin} && pip-compile ${PIP_COMPILE_FLAGS}"
        ((count++))
    fi
done
echo "${echo_verb}ed ${count} plugins"
