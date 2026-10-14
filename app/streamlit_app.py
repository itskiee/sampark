"""sampark dashboard (brief "OUTPUT: a map dashboard, plus a one-page situation report").

Runs locally only (see .streamlit/config.toml: localhost, no usage statistics, no error
details in the browser). Every number shown is read from a run's results.json; every map is
our own (Copernicus DEM hillshade + Sentinel layers), no external map tiles.

Start:  python -m sampark dashboard     (or: python -m streamlit run app/streamlit_app.py)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from sampark import attribution
from sampark.config import REPO_ROOT, load_config, load_event

st.set_page_config(page_title="Sampark — educational prototype", layout="wide")


# --- helpers -------------------------------------------------------------------------------
def runs_dir() -> Path:
    cfg = load_config()
    return cfg.resolve(cfg.output.dir)


def list_runs() -> list[Path]:
    root = runs_dir()
    if not root.is_dir():
        return []
    return sorted((p.parent for p in root.glob("*/results.json")),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def load_results(run: Path) -> dict:
    return json.loads((run / "results.json").read_text(encoding="utf-8"))


def stage(res: dict, name: str) -> dict | None:
    st_ = res.get("stages", {}).get(name)
    return st_ if st_ and st_.get("status") in ("ok", "partial") else None


def missing(res: dict, name: str) -> None:
    st_ = res.get("stages", {}).get(name)
    msg = st_.get("message") if st_ else "this stage was not run"
    st.info(f"Not available: {msg}")


def show_png(run: Path, rel: str | None, caption: str = "") -> None:
    if rel and (run / rel).is_file():
        st.image(str(run / rel), caption=caption, width="stretch")


def download(run: Path, rel: str | None, label: str, mime: str) -> None:
    if rel and (run / rel).is_file():
        st.download_button(label, (run / rel).read_bytes(), file_name=Path(rel).name, mime=mime,
                           key=f"dl-{rel}")


def table(rows, columns: list[tuple[str, str]]) -> None:
    """A readable table: only the listed columns, plain-language headers, yes/no instead of
    True/False, lists as text (never raw program output)."""
    if rows is None or len(rows) == 0:
        st.caption("(none)")
        return
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    keep = [(k, label) for k, label in columns if k in df.columns]
    df = df[[k for k, _ in keep]].rename(columns=dict(keep))
    for c in df.columns:
        if df[c].dtype == bool:
            df[c] = df[c].map({True: "yes", False: "no"})
        elif df[c].dtype == object:
            df[c] = [", ".join(map(str, v)) if isinstance(v, list | tuple) else v for v in df[c]]
    st.dataframe(df, hide_index=True, width="stretch")


LAT_LON = [("lat", "Latitude"), ("lon", "Longitude")]


def start_analysis(aoi, event_date, name, focus, flood_start, event, later_passes: bool) -> None:
    """Run the pipeline with live progress, then show the new run (one code path for the
    sidebar and for 'Track later passes for this area')."""
    from sampark.aoi import AOIError
    from sampark.config import ConfigError
    from sampark.pipeline import run_pipeline

    with st.status("Running the analysis (radar, terrain, OSM, damage, cut-off)…",
                   expanded=True) as status:
        def progress(stage_name: str, message: str) -> None:
            status.write(f"**{stage_name}** — {message[:200]}")

        try:
            config = load_config(event, overrides={"multidate": {"enabled": later_passes}})
            results = run_pipeline(aoi, event_date, config, event_name=name,
                                   progress=progress, focus_bbox=focus,
                                   flood_path_start=flood_start)
            st.session_state["run"] = str(results.run_dir)
            status.update(label="Analysis finished", state="complete")
        except (AOIError, ConfigError, ValueError) as exc:
            status.update(label="Input problem", state="error")
            st.error(str(exc))
            return
    st.session_state["show_runs"] = True
    st.rerun()


def footer(res: dict | None) -> None:
    text = attribution.footer_text()
    if res and stage(res, "segmentation"):
        text += " Training data: " + attribution.SEN1FLOODS11_CITATION
    st.divider()
    st.caption(text)


# --- sidebar: new analysis or previous run --------------------------------------------------
st.sidebar.title("Sampark")
st.sidebar.caption("Who is cut off after a flood, mapped from space (\"sampark\" means "
                   "connection in Hindi and Nepali). Educational "
                   "prototype — not an operational tool.")
if st.session_state.pop("show_runs", False):  # after a finished analysis (set before drawing)
    st.session_state["mode"] = "Open a previous run"
mode = st.sidebar.radio("Mode", ["Open a previous run", "New analysis"], key="mode")

if mode == "New analysis":
    ev_dir = REPO_ROOT / "configs" / "events"
    events = sorted(ev_dir.rglob("*.yaml"))  # includes configs/events/unseen/ test places
    choice = st.sidebar.selectbox("Area", ["Custom area"] + [
        e.relative_to(ev_dir).with_suffix("").as_posix() for e in events])
    flood_start, ev = None, None
    if choice == "Custom area":
        c1, c2 = st.sidebar.columns(2)
        min_lon = c1.number_input("min lon", value=85.28, format="%.4f")
        min_lat = c2.number_input("min lat", value=28.10, format="%.4f")
        max_lon = c1.number_input("max lon", value=85.42, format="%.4f")
        max_lat = c2.number_input("max lat", value=28.30, format="%.4f")
        event_date = st.sidebar.date_input("Flood date", value=date(2026, 8, 26))
        name = st.sidebar.text_input("Name", value="custom area", max_chars=100)
        aoi, focus = [min_lon, min_lat, max_lon, max_lat], None
    else:
        ev = load_event(REPO_ROOT / "configs" / "events" / f"{choice}.yaml")
        aoi = list(ev.aoi.bbox) if ev.aoi.bbox else ev.aoi.geojson
        event_date, name, focus = ev.event_date, ev.name, ev.focus_bbox
        flood_start = ev.flood_path_start
        st.sidebar.write(f"{ev.name} — flood date {ev.event_date}")
    if st.sidebar.checkbox("Trace a flood path from an upstream point (bonus)",
                           value=flood_start is not None):
        c1, c2 = st.sidebar.columns(2)
        lon = c1.number_input("start lon", value=float(flood_start[0]) if flood_start else 85.40,
                              format="%.4f")
        lat = c2.number_input("start lat", value=float(flood_start[1]) if flood_start else 28.30,
                              format="%.4f")
        flood_start = (lon, lat)
    else:
        flood_start = None
    # optional follow-up (design decision 2026-10-13): off for a new area, on in event files
    # that ask for it (the case study); each event's own settings are used
    track_later = st.sidebar.checkbox(
        "Track later satellite passes: is the water going down, is a lake forming? "
        "(adds about 4–8 min)", value=load_config(ev).multidate.enabled)
    if st.sidebar.button("Run analysis", type="primary"):
        start_analysis(aoi, event_date, name, focus, flood_start, ev, track_later)

runs = list_runs()
if not runs:
    st.title("sampark")
    st.info("No analysis yet. Choose 'New analysis' in the sidebar.")
    footer(None)
    st.stop()

labels = {}
for r in runs:
    try:
        meta = load_results(r)
        labels[str(r)] = f"{meta.get('event_name', '?')} — {meta.get('event_date', '?')} — {r.name}"
    except (OSError, ValueError):
        continue
default = st.session_state.get("run")
keys = list(labels)
run_key = st.sidebar.selectbox("Run", keys, index=keys.index(default) if default in keys else 0,
                               format_func=labels.get)
run = Path(run_key)
res = load_results(run)

# --- header -------------------------------------------------------------------------------------
aoi_info = res.get("aoi", {})
st.title(res.get("event_name", "custom area"))
st.caption(f"Flood date {res.get('event_date')} · area {aoi_info.get('area_km2', 0):,.0f} km² · "
           f"bbox {', '.join(f'{v:.2f}' for v in aoi_info.get('bbox', []))} · run {run.name}")
st.warning(attribution.DISCLAIMER)

conf, dmg, cut = stage(res, "confidence"), stage(res, "damage"), stage(res, "cutoff")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Area potentially affected",
          f"{conf['outputs']['affected_total_km2']:.2f} km²" if conf else "n/a")
m2.metric("Buildings potentially affected",
          f"{dmg['outputs']['buildings']['potentially_affected']:,}" if dmg else "n/a")
m3.metric("Settlements likely cut off by road",
          f"{cut['outputs']['by_network']['motorable']['likely cut off']:,}" if cut else "n/a")
m4.metric("Bridges potentially affected",
          f"{dmg['outputs']['bridges']['potentially_affected']:,}" if dmg else "n/a")

with st.expander("Processing steps (status of every stage)"):
    st.dataframe(pd.DataFrame([{"Step": k, "Status": v["status"], "Seconds": v.get("seconds"),
                                "Result": v.get("message", "")}
                               for k, v in res.get("stages", {}).items()]),
                 hide_index=True, width="stretch")

SECTIONS = ["Situation report", "Where did the flood hit?", "What was damaged?",
            "Who is cut off?", "Rescue priorities", "Landing zones", "Later passes",
            "Flood path (bonus)", "AI model", "Data & limitations"]
# One section at a time (not st.tabs): tabs draw every section, about 10 MB of map images
# for the case study, on every load; a selector draws only the section being viewed.
section = st.radio("Section", SECTIONS, horizontal=True, key="section",
                   label_visibility="collapsed")

if section == SECTIONS[0]:
    rep = stage(res, "report")
    files = rep["outputs"]["files"] if rep else {}
    if not files and st.button("Generate the situation report"):
        from sampark.sitrep import build

        built = build(res, run, run / "report")
        files = {k: str(v.relative_to(run)) for k, v in built.items()}
    c1, c2, c3 = st.columns(3)
    with c1:
        download(run, files.get("pdf"), "Download PDF", "application/pdf")
    with c2:
        download(run, files.get("png"), "Download PNG", "image/png")
    with c3:
        download(run, files.get("txt"), "Download text (radio/SMS)", "text/plain")
    show_png(run, files.get("png"))

if section == SECTIONS[1]:
    if conf:
        o = conf["outputs"]
        st.write(f"Sources: {', '.join(o.get('sources', []))}")
        show_png(run, (o["files"].get("png") or [None])[-1])
        show_png(run, (o["files"].get("png") or [None])[0], "whole area")
        by = o.get("affected_by_confidence_km2", {})
        st.subheader("Potentially affected area by confidence (km²)")
        table([{"high": by.get("high"), "medium": by.get("medium"), "low": by.get("low")}],
              [("high", "High: two or more sources agree"),
               ("medium", "Medium: one source, coherent patch"),
               ("low", "Low: one source, small patch")])
        download(run, o["files"].get("polygons"), "Affected areas (GeoJSON)", "application/json")
    else:
        missing(res, "confidence")

if section == SECTIONS[2]:
    if dmg:
        o = dmg["outputs"]
        show_png(run, (o["files"].get("png") or [None])[-1])
        st.subheader("Most affected settlements")
        table([{**x, "share": f"{x['share']:.0%}"} if isinstance(x.get("share"), float) else x
               for x in o["settlements"]["most_affected"]],
              [("name", "Settlement"), ("buildings", "Buildings"),
               ("potentially_affected_buildings", "Potentially affected"),
               ("of_which_high_confidence", "…of which high confidence"),
               ("share", "Share affected"), ("on_flood_corridor", "On the traced flood path"),
               *LAT_LON])
        st.subheader("Bridges potentially affected")
        table(o["bridges"]["list"],
              [("name", "Bridge"), ("highway", "Road type"), ("motorable", "Motorable"),
               ("evidence", "Evidence"), ("confidence", "Confidence"),
               ("near_settlement", "Near"), ("length_m", "Length (m)"), *LAT_LON])
        for key, label in (("buildings", "Buildings"), ("roads", "Roads"), ("bridges", "Bridges"),
                           ("facilities", "Health facilities")):
            download(run, o["files"].get(key), f"{label} (GeoJSON, ODbL)", "application/json")
    else:
        missing(res, "damage")

if section == SECTIONS[3]:
    if cut:
        o = cut["outputs"]
        st.caption(o["meaning"]["likely cut off"])
        show_png(run, (o["files"].get("png") or [None])[-1])
        st.subheader("Settlements likely cut off by road")
        table(o["cut_off_settlements"],
              [("name", "Settlement"), ("buildings", "Buildings"), ("on_foot", "On foot"),
               ("sentence", "Situation"), *LAT_LON])
        st.subheader("Key breaks (repairing these reconnects the most buildings)")
        table(o["key_breaks"],
              [("road", "Road"), ("is_bridge", "Bridge"),
               ("settlements_cut_off", "Settlements it cuts off"),
               ("buildings_cut_off", "Buildings it cuts off"), *LAT_LON])
        download(run, o["files"].get("settlements"), "All settlements (GeoJSON)",
                 "application/json")
    else:
        missing(res, "cutoff")

if section == SECTIONS[4]:
    pri = stage(res, "priority")
    if pri:
        st.caption(pri["outputs"]["method"])
        csv_rel = pri["outputs"]["files"].get("csv")
        if csv_rel and (run / csv_rel).is_file():
            table(pd.read_csv(run / csv_rel),
                  [("rank", "Rank"), ("tier", "Tier"), ("name", "Settlement"),
                   ("buildings", "Buildings"),
                   ("potentially_affected_buildings", "Potentially affected buildings"),
                   ("of_which_high_confidence", "…of which high confidence"),
                   ("road_status", "By road"), ("foot_status", "On foot"),
                   ("hospital_by_road", "Hospital by road"), ("reasons", "Why"), *LAT_LON])
        download(run, csv_rel, "Download priority list (CSV)", "text/csv")
    else:
        missing(res, "priority")

if section == SECTIONS[5]:
    lz = stage(res, "landing_zones")
    if lz:
        o = lz["outputs"]
        st.warning(o.get("status_wording", ""))
        st.write(f"Candidates for **{o.get('settlements_served', 0)} of "
                 f"{o.get('likely_cut_off_settlements', 0)}** settlements likely cut off by road.")
        show_png(run, (o.get("files", {}).get("png") or [None])[0])
        if o.get("options"):
            table(o["options"],
                  [("settlement", "Settlement"), ("option", "Option"),
                   ("distance_m", "Distance (m, straight line)"),
                   ("height_difference_m", "Height difference (m)"),
                   ("walk_crosses_detected_flood", "Walk crosses detected flood"),
                   ("clear_square_m", "Clear square (m)"), ("slope_deg", "Slope (°)"),
                   *LAT_LON])
        download(run, o.get("files", {}).get("geojson"), "Landing-zone candidates (GeoJSON)",
                 "application/json")
    else:
        missing(res, "landing_zones")

if section == SECTIONS[6]:
    md = stage(res, "multidate")
    if md:
        o = md["outputs"]
        st.caption("Later Sentinel-1 passes of the same orbit track, each compared with the "
                   "same before-image and the same rules, only where flooding is plausible.")
        tl = o["timeline"]
        table([{**t, "observed_share": f"{t['observed_share']:.0%}"} for t in tl],
              [("date", "Date"), ("water_km2", "Water (km²)"),
               ("wet_mud_debris_km2", "Likely wet mud / debris (km²)"),
               ("rough_debris_km2", "Likely rough debris (km²)"),
               ("observed_share", "Area seen by the radar")])
        ch = o["changes_first_to_last"]
        st.subheader(f"Change in water from {tl[0]['date']} to {tl[-1]['date']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Receding (water that disappeared)", f"{ch['receding_water_km2']:.2f} km²")
        c2.metric("New ponding (new water since the first pass)",
                  f"{ch['new_ponding_km2']:.2f} km²")
        c3.metric("Persistent (water on both dates)", f"{ch['persistent_water_km2']:.2f} km²")
        show_png(run, (o["files"].get("png") or [None])[0])
        if o.get("possible_lakes"):
            st.warning("Growing water bodies flagged as POSSIBLE lakes forming (e.g. behind "
                       "debris): verify.")
            table(o["possible_lakes"],
                  [("area_km2_by_date", "Area on each date (km²)"),
                   ("growth_factor", "Growth (× first date)"), ("status", "Status"),
                   *LAT_LON])
    elif ((res.get("stages", {}).get("multidate") or {}).get("message") or "").startswith(
            "Not requested"):
        st.info("**Later satellite passes were not tracked for this analysis** (an optional "
                "follow-up, so the first answer comes sooner). Tracking compares the later "
                "Sentinel-1 passes of the same orbit track with the same before-image: is the "
                "water going down, is new water ponding, is a lake forming behind debris (a "
                "second flood hazard)? It adds about 4–8 minutes; everything already "
                "downloaded for this area is reused.")
        if st.button("Track later passes for this area", type="primary"):
            fp_out = (res.get("stages", {}).get("flood_path") or {}).get("outputs") or {}
            start = fp_out.get("start")
            start_analysis(res["aoi"]["bbox"], date.fromisoformat(res["event_date"]),
                           res.get("event_name", "custom area"), None,
                           (start["lon"], start["lat"]) if start else None, None, True)
    else:
        missing(res, "multidate")

if section == SECTIONS[7]:
    fp = stage(res, "flood_path")
    if fp:
        o = fp["outputs"]
        st.write(f"Traced {o['length_km']} km downstream, elevation drop "
                 f"{o['elevation_start_m'] - o['elevation_end_m']:.0f} m.")
        show_png(run, (o["files"].get("png") or [None])[0])
        if o.get("settlements_along_path"):
            table(o["settlements_along_path"],
                  [("name", "Settlement"), ("place", "Type (OpenStreetMap)"),
                   ("km_downstream", "km downstream"),
                   ("distance_from_path_m", "Distance from the path (m)"),
                   ("height_above_river_m", "Height above the river (m)"),
                   ("low_lying", "Low-lying"), ("buildings", "Buildings"), *LAT_LON])
    else:
        missing(res, "flood_path")
        st.caption("Set an upstream point in the sidebar ('New analysis') to trace a path.")

if section == SECTIONS[8]:
    seg = stage(res, "segmentation")
    if seg:
        o = seg["outputs"]
        st.write(f"**{o['model']}**: our own model, trained from scratch on Sen1Floods11, "
                 "running locally. " + o["note"] + ".")
        show_png(run, (o["files"].get("png") or [None])[0])
        scores = o.get("model_test_scores") or {}
        names = {"test": "Sen1Floods11 test images (held out)",
                 "bolivia": "Bolivia: a whole flood event never seen in training",
                 "valid": "Validation images (used to pick the training epoch)"}
        st.subheader("How well the model works on images it never trained on")
        table([{"data": names.get(k, k), **(v or {})} for k, v in scores.items()],
              [("data", "Data"), ("iou", "IoU (overlap, 1 = perfect)"),
               ("precision", "Precision"), ("recall", "Recall"), ("f1", "F1")])
        st.caption(o.get("domain_shift_note", ""))
    else:
        missing(res, "segmentation")

if section == SECTIONS[9]:
    st.subheader("Data used (allowed inputs only)")
    for name in ("s1", "s2", "terrain", "osm"):
        s = res.get("stages", {}).get(name)
        if s:
            label = {"s1": "Sentinel-1 radar", "s2": "Sentinel-2 optical",
                     "terrain": "Copernicus DEM (terrain)",
                     "osm": "OpenStreetMap before the event"}[name]
            st.write(f"**{label}** ({s['status']}): {s.get('message', '')}")
    st.subheader("What this system cannot do")
    from sampark.sitrep import LIMITATIONS

    for line in LIMITATIONS:
        st.write("- " + line)
    st.subheader("Attributions")
    for line in attribution.DATA_ATTRIBUTIONS:
        st.write("- " + line)
    if stage(res, "segmentation"):
        st.write("- Training data: " + attribution.SEN1FLOODS11_CITATION)

footer(res)
