"""Career taxonomy ingestion and free-text career resolution.

This package owns the career label namespace that the statistics layer conditions
on. Every artifact under ``data/processed/`` is produced by a module here and
consumed by code, never by a human. Diagnostics live in ``dev/`` and are never
imported from this package.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
"""``occupational_scrape/`` -- the directory holding config/, data/, cache/."""

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = PROJECT_ROOT / "cache"

__all__ = [
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "CONFIG_DIR",
    "DATA_DIR",
    "RAW_DIR",
    "INTERIM_DIR",
    "PROCESSED_DIR",
    "CACHE_DIR",
]
