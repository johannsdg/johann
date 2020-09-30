#!/usr/bin/env python3
# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

"""Checks file(s) for the Johann copyright header."""

import re
import sys
from pathlib import Path
from typing import List

from pre_commit import output

EXPECTED_HEADER = (
    "# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.\n"
    "# Use of this source code is governed by a BSD-3-clause license that can\n"
    "# be found in the LICENSE file. See the AUTHORS file for names of contributors.\n"
)


def get_header_lines(fpath: Path) -> List[str]:
    h_lines = []
    with fpath.open() as f:
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
                h_lines.append(line)

            if len(h_lines) == 3:
                break
    return h_lines


if __name__ == "__main__":
    retv = 0
    for filename in sys.argv[1:]:
        filepath = Path(filename)
        if not filepath.is_file():
            continue

        header_lines = get_header_lines(filepath)
        if len(header_lines) < 3:
            retv |= 1
            output.write_line(f"{filename}: (not enough lines)")
        else:
            header = "".join(header_lines)
            if header != EXPECTED_HEADER:
                retv |= 1
                output.write(f"{filename}:\n  {'  '.join(header_lines)}")
            else:
                retv |= 0
    sys.exit(retv)
