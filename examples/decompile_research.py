r"""Decompile a Dreamehome APK with jadx and scan the resulting sources.

This is a convenience wrapper around two steps:

    jadx -d C:\Temp\dreamehome-jadx C:\path\to\dreamehome.apk
    python examples/source_research.py C:\Temp\dreamehome-jadx

It does not install Java or jadx. If jadx is not on PATH, pass --jadx-path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dreame_lawn_mower_client import (
    DEFAULT_APK_RESEARCH_TERMS,
    analyze_decompiled_sources,
    run_jadx_decompile,
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
        "--output-dir",
        default=r"C:\Temp\dreamehome-jadx",
        help="Directory for jadx output.",
    )
    parser.add_argument("--jadx-path", help="Path to jadx or jadx.bat.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow reuse of a non-empty output directory.",
    )
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        help="Search term. Can be passed multiple times.",
    )
    parser.add_argument("--max-snippets", type=int, default=40)
    parser.add_argument("--max-files", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=None)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    decompile = run_jadx_decompile(
        Path(args.apk),
        output_dir,
        jadx_path=args.jadx_path,
        overwrite=args.overwrite,
        timeout=args.timeout,
    )
    result: dict[str, object] = {"decompile": decompile}
    if decompile.get("ok"):
        terms = tuple(args.terms) if args.terms else DEFAULT_APK_RESEARCH_TERMS
        result["source"] = analyze_decompiled_sources(
            output_dir,
            terms=terms,
            max_snippets_per_term=args.max_snippets,
            max_files_per_term=args.max_files,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
