"""Stage "exports": the offline field package (GeoPackage, KML, GPX, sit-rep, README).

Runs last and uses whatever earlier stages produced; missing layers are simply not in the
package (the README lists what is inside).
"""

from __future__ import annotations

from floodmap.exports import collect_layers, readme, write_package
from floodmap.pipeline import StageContext, register_stage
from floodmap.results import StageResult, StageStatus


def _file(ctx: StageContext, stage: str, key: str):
    st = ctx.results.stages.get(stage)
    return st.outputs.get("files", {}).get(key) if st is not None and st.usable else None


@register_stage("exports", uses=("confidence", "damage", "cutoff", "priority", "landing_zones",
                                 "multidate", "flood_path", "report"))
def run_exports(ctx: StageContext) -> StageResult:
    files = {
        "flood_areas": _file(ctx, "confidence", "polygons"),
        "buildings_affected": _file(ctx, "damage", "buildings"),
        "roads_affected": _file(ctx, "damage", "roads"),
        "bridges_affected": _file(ctx, "damage", "bridges"),
        "health_affected": _file(ctx, "damage", "facilities"),
        "settlements_cutoff": _file(ctx, "cutoff", "settlements"),
        "priority": _file(ctx, "priority", "geojson"),
        "landing_zones": _file(ctx, "landing_zones", "geojson"),
        "key_breaks": _file(ctx, "cutoff", "breaks"),
        "flood_path": _file(ctx, "flood_path", "path"),
        "possible_lakes": _file(ctx, "multidate", "lakes"),
    }
    layers = collect_layers(files)
    if not layers:
        return StageResult("exports", StageStatus.SKIPPED,
                           "Nothing to export yet (no map layers were produced).")
    meta = {"event_name": ctx.results.event_name, "event_date": ctx.results.event_date,
            "run_id": ctx.results.run_id}
    report = ctx.results.stages.get("report")
    rep_files = report.outputs.get("files", {}) if report is not None and report.usable else {}
    pkg = write_package(layers, ctx.results.run_dir / "exports", ctx.results.run_id,
                        {"sitrep.pdf": rep_files.get("pdf"), "sitrep.txt": rep_files.get("txt")},
                        readme(meta, layers))
    size_mb = pkg["zip"].stat().st_size / 1e6
    return StageResult("exports", StageStatus.OK,
                       f"field package ({size_mb:.1f} MB): {len(layers)} layers as GeoPackage + "
                       f"KML, GPS waypoints, situation report", outputs={
                           "layers": pkg["layers"], "files": {"zip": pkg["zip"],
                                                              "gpkg": pkg["gpkg"],
                                                              "gpx": pkg["gpx"]}})
