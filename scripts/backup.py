"""
Backup script for PT Generator.

Creates a timestamped ZIP of the data/ and outputs/ directories and prunes
old backups beyond the configured retention count.

Usage:
    python scripts/backup.py

Environment variables:
    DATA_DIR      Path to the data directory (default: ./data)
    OUTPUTS_DIR   Path to the outputs directory (default: ./outputs)
    BACKUP_DIR    Where to write backup archives (default: ./backups)
    BACKUP_KEEP   Number of recent backups to retain (default: 7)

Deploy as a Railway cron service (daily at 03:00 UTC) pointing to this repo
with start command: python scripts/backup.py
"""

import datetime
import os
import shutil
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", "./outputs"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "./backups"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))


def run() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for src_dir, label in [(DATA_DIR, "data"), (OUTPUTS_DIR, "outputs")]:
        if not src_dir.exists():
            print(f"Skipping {label}: {src_dir} does not exist")
            continue
        archive_base = str(BACKUP_DIR / f"backup_{label}_{ts}")
        shutil.make_archive(archive_base, "zip", src_dir.parent, src_dir.name)
        print(f"Backup written: {archive_base}.zip")

    # Prune each label's old backups independently
    for label in ("data", "outputs"):
        archives = sorted(BACKUP_DIR.glob(f"backup_{label}_*.zip"))
        for old in archives[:-BACKUP_KEEP]:
            old.unlink()
            print(f"Pruned: {old}")


if __name__ == "__main__":
    run()
