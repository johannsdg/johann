#!/bin/bash
#
# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

: "${JOHANN_PYTHON:=/usr/bin/python3}"

cat > /tmp/pmtr_conductor.conf << EOF
listen on udp://127.0.0.1:31337
job {
    name johann_conductor
    cmd /bin/bash -c "$JOHANN_PYTHON johann_main.py"
    env JOHANN_MODE=conductor
    dir /opt/johann/johann
    depends {
      /opt/johann/johann/.dev_reload_file
    }
}
EOF

echo "PMTR CONF:"
cat /tmp/pmtr_conductor.conf

echo "STARTING PMTR"
pmtr -vIFc /tmp/pmtr_conductor.conf > /tmp/log 2>&1 &

touch /tmp/log
tail -f /tmp/log
