"""Create a local .env from .env.example, replacing every `replace-me` placeholder with a random secret.

Usage: python3 scripts/generate_env.py [--force]
Refuses to overwrite an existing .env unless --force is given.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    target = ROOT / ".env"
    if target.exists() and "--force" not in sys.argv:
        print(".env already exists; pass --force to regenerate (this rotates all local secrets).")
        return 1
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.strip().endswith("=replace-me"):
            key = line.split("=", 1)[0]
            line = f"{key}={secrets.token_urlsafe(32)}"
        lines.append(line)
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)
    print(f"Wrote {target} with fresh random secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
