#!/usr/bin/env python3
# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

"""Checks file(s) for the Johann copyright header."""

import re
import sys
from pathlib import Path

from pre_commit import output

EXPECTED_HEADER = (
    "# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.\n"
    "# Use of this source code is governed by a BSD-3-clause license that can\n"
    "# be found in the LICENSE file. See the AUTHORS file for names of contributors.\n"
)


if __name__ == "__main__":
    retv = 0
    for filename in sys.argv[1:]:
        filepath = Path(filename)
        if not filepath.is_file():
            continue

        lines_to_check = []
        with filepath.open() as f:
            for line in f:
                # skip shebang
                if re.match(r'#![/"].*', line):
                    continue
                # skip encoding declaration
                elif re.search(r"coding[=:]\s*([-\w.]+)", line):
                    continue
                # skip blank lines
                elif line.strip() in ["", "#"]:
                    continue
                else:
                    lines_to_check.append(line)

                if len(lines_to_check) == 3:
                    break

        if len(lines_to_check) < 3:
            retv |= 1
            output.write_line(f"{filename}: (not enough lines)")
        else:
            header = "".join(lines_to_check)
            if header != EXPECTED_HEADER:
                retv |= 1
                output.write(f"{filename}:\n  {'  '.join(lines_to_check)}")
            else:
                retv |= 0
    sys.exit(retv)
