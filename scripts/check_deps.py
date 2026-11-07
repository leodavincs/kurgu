#!/usr/bin/env python3
"""Check Kurgu's dependencies (same as `kurgu doctor`). Exit 0 = all good, 1 = something missing. Flags: --json, --fix."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import doctor  # noqa: E402

if __name__ == "__main__":
    sys.exit(doctor.main(sys.argv[1:]))
