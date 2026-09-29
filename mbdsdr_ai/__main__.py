# SPDX-License-Identifier: MIT
"""``python -m mbdsdr`` 入口：转发到 core.cli。"""

import sys

from .core.cli import main

if __name__ == "__main__":
    sys.exit(main())
