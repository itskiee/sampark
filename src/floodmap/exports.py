"""Field exports (M8 extra from the owner's list): one zip a field team can use offline.

Contents: layers.gpkg (every layer, for GIS desks), kml/*.kml (Google Earth / phone apps),
waypoints.gpx (handheld GPS: priority settlements, landing-zone candidates, key breaks),
the situation report, a README, the required attributions and the ODbL notice for
OSM-derived data. Only our own results are exported: no new data.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from floodmap import attribution

KML_SIMPLIFY_M = 5.0  # keeps KML small; well below the 20 m analysis resolution


def _read(path) -> gpd.GeoDataFrame | None:
    if path is None or not Path(path).is_file():
        return None
    gdf = gpd.read_file(path)
    return None if gdf.empty else gdf


def _named(gdf: gpd.GeoDataFrame, name_col: str | None, desc_cols: list[str]) -> gpd.GeoDataFrame:
    """Add Name/Description (shown by Google Earth and GPS units)."""
    out = gdf.copy()
    for c in out.columns:  # list-valued fields (e.g. areas by date) -> readable text
        if c != "geometry" and out[c].dtype == object:
            out[c] = [", ".join(map(str, v)) if isinstance(v, list | tuple | np.ndarray) else v
                      for v in out[c]]
    label = out[name_col].astype(str) if name_col and name_col in out else "feature"
    # GeoPackage/KML field names are case-insensitive: keep OSM "name" as "osm_name"
    out = out.rename(columns={c: f"osm_{c.lower()}" for c in out.columns
                              if c.lower() in ("name", "description")})
    out["Name"] = label
    present = [c for c in desc_cols if c in out]
    out["Description"] = [" | ".join(f"{c}: {r[c]}" for c in present if pd.notna(r[c]))
                          for _, r in out.iterrows()]
    return out


def collect_layers(files: dict[str, Path | None]) -> dict[str, gpd.GeoDataFrame]:
    """Every exportable layer that exists, in WGS84, with Name/Description."""
    spec = {
        "flood_areas": (None, ["class", "confidence", "area_m2", "status"]),
        "buildings_affected": ("building", ["flood_class", "confidence", "settlement_id"]),
        "roads_affected": ("name", ["highway", "affected_m", "confidence", "flood_class"]),
        "bridges_affected": ("label", ["highway", "evidence", "confidence", "near_place"]),
        "health_affected": ("label", ["category", "confidence", "flood_class"]),
        "settlements_cutoff": ("name", ["sentence", "building_count", "motorable_any",
                                        "all_ways_any"]),
        "priority": ("name", ["rank", "tier", "reasons", "buildings",
                              "potentially_affected_buildings"]),
        "landing_zones": ("settlement", ["option", "distance_m", "height_difference_m",
                                         "walk_crosses_detected_flood", "clear_square_m",
                                         "status"]),
        "key_breaks": ("road", ["settlements_cut_off", "buildings_cut_off", "is_bridge"]),
        "flood_path": (None, ["length_km"]),
        "possible_lakes": ("status", ["area_km2_by_date", "growth_factor"]),
    }
    layers = {}
    for key, (name_col, desc) in spec.items():
        gdf = _read(files.get(key))
        if gdf is None:
            continue
        gdf = gdf.to_crs(4326)
        if key == "flood_areas":
            gdf["status"] = gdf.get("status", "potentially affected")
            gdf["label"] = [f"{c} ({k} confidence)" for c, k in
                            zip(gdf["class"], gdf["confidence"], strict=True)]
            name_col = "label"
        if key == "settlements_cutoff":
            gdf = gdf[gdf["motorable_any"].isin(["likely cut off", "possibly cut off"])]
        if key == "flood_path":
            gdf["label"] = "traced flood path"
            name_col = "label"
        layers[key] = _named(gdf, name_col, desc)
    return layers


def write_package(layers: dict[str, gpd.GeoDataFrame], out_dir: Path, run_id: str,
                  extra_files: dict[str, Path | None], readme_lines: list[str]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "layers.gpkg"
    gpkg.unlink(missing_ok=True)
    kml_dir = out_dir / "kml"
    kml_dir.mkdir(exist_ok=True)
    written = {}
    for key, gdf in layers.items():
        safe = gdf.copy()
        for c in safe.columns:  # GPKG/KML cannot store numpy bools reliably
            if c != "geometry" and safe[c].dtype == bool:
                safe[c] = safe[c].astype(int)
        safe.to_file(gpkg, layer=key, driver="GPKG")
        kml = safe[["Name", "Description", "geometry"]].copy()
        if key == "flood_areas":  # simplify in metres, then back to WGS84
            utm = kml.estimate_utm_crs()
            kml = kml.to_crs(utm)
            kml["geometry"] = kml.geometry.simplify(KML_SIMPLIFY_M)
            kml = kml.to_crs(4326)
        kml.to_file(kml_dir / f"{key}.kml", driver="KML")
        written[key] = len(gdf)

    gpx = out_dir / "waypoints.gpx"
    gpx.unlink(missing_ok=True)
    points = []
    for key, prefix in (("priority", "PRIORITY"), ("landing_zones", "LZ-CANDIDATE"),
                        ("key_breaks", "BREAK"), ("possible_lakes", "POSSIBLE-LAKE")):
        if key in layers:
            g = layers[key]
            g = g[g.geometry.geom_type == "Point"]
            if key == "priority" and "tier" in g:  # GPS units hold limited waypoints: only
                g = g[g["tier"] <= 3]  # the actionable tiers (full list in KML/GeoPackage)
            points.append(gpd.GeoDataFrame({
                "name": [f"{prefix} {n}"[:60] for n in g["Name"]],
                "desc": g["Description"].str.slice(0, 250)}, geometry=g.geometry.values,
                crs=4326))
    if points:
        gpd.GeoDataFrame(pd.concat(points, ignore_index=True), crs=4326).to_file(
            gpx, driver="GPX", layer="waypoints")

    readme = out_dir / "README.txt"
    readme.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    lic = out_dir / "LICENSE_OSM_DATA.txt"
    lic.write_text(attribution.OSM_DATA_LICENSE + "\n", encoding="utf-8")

    zip_path = out_dir / f"floodmap_{run_id}_field_package.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in [readme, lic, gpkg, *sorted(kml_dir.glob("*.kml"))] + \
                ([gpx] if gpx.is_file() else []):
            zf.write(p, p.relative_to(out_dir).as_posix())
        for arcname, p in extra_files.items():
            if p is not None and Path(p).is_file():
                zf.write(p, arcname)
    return {"layers": written, "zip": zip_path, "gpkg": gpkg,
            "gpx": gpx if gpx.is_file() else None}


def readme(results_meta: dict, layers: dict[str, gpd.GeoDataFrame]) -> list[str]:
    lines = [
        "floodmap field package",
        "======================",
        attribution.DISCLAIMER,
        "",
        f"Event: {results_meta.get('event_name')} | flood date {results_meta.get('event_date')}"
        f" | run {results_meta.get('run_id')}",
        "",
        "Files:",
        "  sitrep.pdf / sitrep.txt  one-page situation report (text version for radio/SMS)",
        "  layers.gpkg              all layers below in one GeoPackage (QGIS/ArcGIS)",
        "  kml/<layer>.kml          the same layers for Google Earth / phone map apps",
        "  waypoints.gpx            GPS waypoints: PRIORITY settlements (tiers 1-3), "
        "LZ-CANDIDATE landing zones, BREAK road/bridge breaks",
        "",
        "Layers (features):",
        *[f"  {k:<20} {len(v):>7,}" for k, v in layers.items()],
        "",
        "Meaning: 'potentially affected' and 'likely cut off' are estimates from satellite",
        "change detection and pre-event OpenStreetMap. Landing zones are CANDIDATES: verify",
        "trees, power lines, ground and approach on site. Verify everything on the ground.",
        "",
        "Attributions:",
        *[f"  {a}" for a in attribution.DATA_ATTRIBUTIONS],
        f"  Training data of the water model: {attribution.SEN1FLOODS11_CITATION}",
        "OSM-derived layers: Open Database License (ODbL) 1.0, see LICENSE_OSM_DATA.txt.",
    ]
    return lines
