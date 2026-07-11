"""Embed a reproducibly compressed XP2P host worker in the Python package."""

from __future__ import annotations

import argparse
import base64
import hashlib
import textwrap
from pathlib import Path


def main() -> None:
    """Write the generated worker module from a binary and its gzip archive."""
    parser = argparse.ArgumentParser()
    parser.add_argument("worker", type=Path)
    parser.add_argument("compressed_worker", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    worker = args.worker.read_bytes()
    compressed = args.compressed_worker.read_bytes()
    encoded_lines = "\n".join(
        f'    "{line}"'
        for line in textwrap.wrap(base64.b64encode(compressed).decode(), width=100)
    )
    source = (
        '"""Reproducible compressed AArch64/Bionic XP2P host worker."""\n\n'
        f'WORKER_GZIP_SHA256 = "{hashlib.sha256(compressed).hexdigest()}"\n'
        f'WORKER_SHA256 = "{hashlib.sha256(worker).hexdigest()}"\n'
        "WORKER_GZIP_BASE64 = (\n"
        f"{encoded_lines}\n"
        ")\n"
    )
    args.output.write_text(source, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
