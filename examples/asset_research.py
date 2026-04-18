r"""Inspect extracted Dreamehome Flutter/plugin assets for map clues.

Point this at one extracted asset directory, for example:

    python examples/asset_research.py C:\Temp\dreamehome\assets\flutter_assets
    python examples/asset_research.py C:\Temp\dreamehome\assets\plugin

The output is intentionally compact so it can be attached to issues without
dumping large vendor bundles or binary payloads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dreame_lawn_mower_client import (
    DEFAULT_DREAMEHOME_ASSET_TERMS,
    analyze_dreamehome_assets,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_dir",
        help="Path to extracted Dreamehome assets, Flutter assets, or plugin assets.",
    )
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        help="Search term. Can be passed multiple times.",
    )
    parser.add_argument("--max-snippets", type=int, default=30)
    parser.add_argument("--max-files", type=int, default=40)
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=500,
        help="Skip or compact source lines longer than this many characters.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    terms = tuple(args.terms) if args.terms else DEFAULT_DREAMEHOME_ASSET_TERMS
    result = analyze_dreamehome_assets(
        Path(args.source_dir),
        terms=terms,
        max_snippets_per_term=args.max_snippets,
        max_files_per_term=args.max_files,
        max_line_length=args.max_line_length,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
