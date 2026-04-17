"""Inspect a Dreamehome APK for protocol-related strings.

This is a lightweight string index, not a full decompiler. It is useful when a
new app version appears and we want to confirm endpoints, property names, and
camera/map hints before trying live mower commands.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dreame_lawn_mower_client import (
    DEFAULT_APK_RESEARCH_TERMS,
    analyze_dreamehome_apk,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "apk",
        nargs="?",
        default=(
            r"C:\Users\przemyslaw.klys.EVOTEC\Downloads"
            r"\uptodown-com.dreame.smartlife.apk"
        ),
        help="Path to a Dreamehome APK.",
    )
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        help="Search term. Can be passed multiple times.",
    )
    parser.add_argument("--max-strings", type=int, default=40)
    parser.add_argument("--max-files", type=int, default=30)
    parser.add_argument(
        "--max-string-length",
        type=int,
        default=500,
        help="Skip printable strings longer than this many characters.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    terms = tuple(args.terms) if args.terms else DEFAULT_APK_RESEARCH_TERMS
    result = analyze_dreamehome_apk(
        Path(args.apk),
        terms=terms,
        max_strings_per_term=args.max_strings,
        max_files_per_term=args.max_files,
        max_string_length=args.max_string_length,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
