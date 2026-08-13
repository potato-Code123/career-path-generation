"""Diagnostic: print what is actually inside the pinned O*NET archive.

Answers "what did we download, and does it say what we think it says" before any
build script is trusted. Output is for a human; nothing in ``src/`` reads it.

    PYTHONPATH=src python dev/inspect_onet_release_contents.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from occupational_scrape.fetch_source_files import (  # noqa: E402
    ONET_TABLES,
    read_all_occupations,
    read_manifest,
    read_onet_readme,
    read_onet_table,
    raw_path,
)


def main() -> None:
    manifest = read_manifest()
    print("=== SOURCE_MANIFEST.yaml ===")
    for key, entry in manifest.items():
        print(f"  {key}")
        print(f"      file    {entry['filename']}")
        print(f"      release {entry['release_label']}")
        print(f"      sha256  {entry['sha256']}")
        print(f"      fetched {entry['retrieved_utc']} ({entry.get('acquisition', '?')})")

    print("\n=== O*NET archive Read Me (first 5 lines) ===")
    for line in read_onet_readme().splitlines()[:5]:
        print(f"  {line}")

    print("\n=== archive members ===")
    with zipfile.ZipFile(raw_path("onet_database")) as archive:
        members = sorted(archive.namelist())
    print(f"  {len(members)} members; the three this phase uses:")
    for label, member in ONET_TABLES.items():
        match = next((name for name in members if name.endswith("/" + member)), "MISSING")
        print(f"      {label:<28} {match}")

    print("\n=== table shapes ===")
    for label in ONET_TABLES:
        frame = read_onet_table(label)
        codes = frame["O*NET-SOC Code"].nunique()
        print(f"  {label:<28} {len(frame):>6} rows, {codes:>5} distinct codes")
        print(f"      columns: {list(frame.columns)}")

    print("\n=== All_Occupations.csv (Data-level source) ===")
    all_occupations = read_all_occupations()
    print(f"  {len(all_occupations)} rows, columns: {list(all_occupations.columns)}")
    blank = all_occupations[all_occupations["Data-level"].isna()]
    print(f"  {len(blank)} rows with blank Data-level (residual 'All Other' buckets)")
    print("  ECE-relevant residuals excluded from the leaf set:")
    for code, title in zip(blank["Code"], blank["Occupation"]):
        if str(code).startswith(("17-2", "17-3", "15-12", "15-2")):
            print(f"      {code}  {title}")


if __name__ == "__main__":
    main()
