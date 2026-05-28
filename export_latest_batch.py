#!/usr/bin/env python3
"""Zip the newest batch folder into exports/ for easy sharing."""

from __future__ import annotations

import sys
import zipfile
from datetime import datetime
from pathlib import Path


def _get_latest_batch_dir(drafts_dir: Path) -> Path | None:
    batches = [path for path in drafts_dir.glob("batch_*") if path.is_dir()]
    if not batches:
        return None
    return max(batches, key=lambda path: path.stat().st_mtime)


def _build_zip_path(exports_dir: Path, batch_dir: Path) -> Path:
    zip_path = exports_dir / f"{batch_dir.name}.zip"
    if not zip_path.exists():
        return zip_path
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    return exports_dir / f"{batch_dir.name}_{suffix}.zip"


def main() -> int:
    drafts_dir = Path("drafts")
    latest_batch = _get_latest_batch_dir(drafts_dir)
    if latest_batch is None:
        print("No batch folder found under drafts/", file=sys.stderr)
        return 1

    exports_dir = Path("exports")
    exports_dir.mkdir(parents=True, exist_ok=True)
    zip_path = _build_zip_path(exports_dir=exports_dir, batch_dir=latest_batch)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in sorted(latest_batch.rglob("*")):
            if file_path.is_file():
                arcname = Path(latest_batch.name) / file_path.relative_to(latest_batch)
                zip_file.write(file_path, arcname)

    print(f"Export zip: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
