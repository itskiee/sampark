"""Unpack the EMSR927 packages the owner downloaded (checking only, never a pipeline input).

Input:  validation/data/EMSR927/*.zip (+ EMSR927-aois.geojson), downloaded by the owner from
        https://mapping.emergency.copernicus.eu/activations/EMSR927/
Output: validation/data/EMSR927/extracted/<package>/... (git-ignored)

Safety: archives are untrusted. Only data/document file types are extracted; any member
whose path is absolute or leaves its folder ("zip slip") is refused; nothing is executed.
Zips nested inside zips are unpacked one level, each into its own folder.
Credit: European Union, Copernicus Emergency Management Service data.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent / "data" / "EMSR927"
OUT = ROOT / "extracted"
DATA_TYPES = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".json", ".geojson", ".gpkg", ".xml",
              ".sld", ".lyr", ".pdf", ".tif", ".tiff", ".jpg", ".png", ".kml", ".kmz", ".txt",
              ".qml", ".csv"}


def safe_target(folder: Path, member: str) -> Path | None:
    path = PurePosixPath(member.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return None
    target = (folder / Path(*path.parts)).resolve()
    return target if target.is_relative_to(folder.resolve()) else None


def unpack(data: bytes, folder: Path, log: list[str], depth: int = 0) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        seen = set()
        for info in zf.infolist():
            if info.is_dir() or info.filename in seen:
                continue  # duplicate entries in the archive: first one wins
            seen.add(info.filename)
            suffix = PurePosixPath(info.filename).suffix.lower()
            if suffix == ".zip" and depth == 0:
                inner = folder / PurePosixPath(info.filename).stem
                unpack(zf.read(info), inner, log, depth + 1)
                continue
            if suffix not in DATA_TYPES:
                log.append(f"skipped (type {suffix or '?'}): {info.filename}")
                continue
            target = safe_target(folder, info.filename)
            if target is None:
                log.append(f"REFUSED unsafe path: {info.filename}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))


def main() -> int:
    log: list[str] = []
    zips = sorted(ROOT.glob("*.zip"))
    if not zips:
        print(f"No zips in {ROOT}")
        return 1
    for z in zips:
        unpack(z.read_bytes(), OUT / z.stem, log)
    files = [p for p in OUT.rglob("*") if p.is_file()]
    print(f"extracted {len(files)} files from {len(zips)} packages into {OUT}")
    for line in log:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
