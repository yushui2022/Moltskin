#!/usr/bin/env python3
"""Start Moltskin.

Works from a fresh clone without installing the package:

    python run.py

Environment:
    MOLTSKIN_HOST  default 127.0.0.1
    MOLTSKIN_PORT  default 8765
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Make `import moltskin` work from any working directory.
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402


def main() -> None:
    host = os.environ.get("MOLTSKIN_HOST", "127.0.0.1")
    port = int(os.environ.get("MOLTSKIN_PORT", "8765"))
    uvicorn.run("moltskin.services.core.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
