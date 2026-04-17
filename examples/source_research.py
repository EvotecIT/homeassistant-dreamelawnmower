r"""Inspect jadx/decompiled Dreamehome sources for protocol-related code.

Run jadx separately first, for example:

    jadx -d C:\Temp\dreamehome-jadx C:\path\to\dreamehome.apk

Then point this helper at the output directory. It searches source-like files
for the same protocol terms used by the APK string scanner and returns compact
file/line snippets suitable for sharing in issues or research notes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dreame_lawn_mower_client import (
    DEFAULT_APK_RESEARCH_TERMS,
    analyze_decompiled_sources,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_dir",
        help="Path to a jadx/decompiled Dreamehome source directory.",
    )
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        help="Search term. Can be passed multiple times.",
    )
    parser.add_argument("--max-snippets", type=int, default=40)
    parser.add_argument("--max-files", type=int, default=50)
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=500,
        help="Skip or compact source lines longer than this many characters.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    terms = tuple(args.terms) if args.terms else DEFAULT_APK_RESEARCH_TERMS
    result = analyze_decompiled_sources(
        Path(args.source_dir),
        terms=terms,
        max_snippets_per_term=args.max_snippets,
        max_files_per_term=args.max_files,
        max_line_length=args.max_line_length,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
