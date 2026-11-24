"""Print the pinned Chromium commit after checking its official release tag."""

import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen


def verified_commit():
    pin = json.loads(Path(__file__).with_name("upstream.json").read_text(encoding="utf-8"))
    version, expected = pin["version"], pin["commit"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version):
        raise ValueError("invalid Chromium version in upstream.json")
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("invalid Git commit in upstream.json")

    url = f"https://chromium.googlesource.com/chromium/src/+/refs/tags/{version}?format=JSON"
    with urlopen(url, timeout=30) as response:
        prefix = response.readline().rstrip()
        if prefix != b")]}'":
            raise ValueError("unexpected Gitiles response")
        actual = json.load(response)["commit"]
    if actual != expected:
        raise ValueError(f"tag {version} resolves to {actual}, expected {expected}")
    return expected


if __name__ == "__main__":
    try:
        print(verified_commit())
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"Chromium pin verification failed: {error}")
