"""floodmap dashboard (brief "OUTPUT: a map dashboard, plus a one-page situation report").

Runs locally only (see .streamlit/config.toml: localhost, no usage statistics, no error
details in the browser). Every number shown is read from a run's results.json; every map is
our own (Copernicus DEM hillshade + Sentinel layers), no external map tiles.

Start:  python -m floodmap dashboard     (or: python -m streamlit run app/streamlit_app.py)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from floodmap import attribution
from floodmap.config import REPO_ROOT, load_config, load_event

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


def start_analysis(aoi, event_date, name, focus, flood_start, event, later_passes: bool) -> None:
    """Run the pipeline with live progress, then show the new run (one code path for the
    sidebar and for 'Track later passes for this area')."""
    from floodmap.aoi import AOIError
    from floodmap.config import ConfigError
    from floodmap.pipeline import run_pipeline

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
                   "connection in Hindi and Nepali; code package: floodmap). Educational "
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
    # optional follow-up (owner decision 2026-10-13): off for a new area, on in event files
    # that ask for it (the case study); each event's own settings are used
    track_later = st.sidebar.checkbox(
        "Track later satellite passes: is the water going down, is a lake forming? "
        "(adds about 4–8 min)", value=load_config(ev).multidate.enabled)
    if st.sidebar.button("Run analysis", type="primary"):
        start_analysis(aoi, event_date, name, focus, flood_start, ev, track_later)

runs = list_runs()
if not runs:
    st.title("floodmap")
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
    st.dataframe(pd.DataFrame([{"stage": k, "status": v["status"], "seconds": v.get("seconds"),
                                "message": v.get("message", "")}
                               for k, v in res.get("stages", {}).items()]),
                 hide_index=True, width="stretch")

tabs = st.tabs(["Situation report", "Where did the flood hit?", "What was damaged?",
                "Who is cut off?", "Rescue priorities", "Landing zones", "Later passes",
                "Flood path (bonus)", "AI model", "Data & limitations"])

with tabs[0]:
    rep = stage(res, "report")
    files = rep["outputs"]["files"] if rep else {}
    if not files and st.button("Generate the situation report"):
        from floodmap.sitrep import build

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

with tabs[1]:
    if conf:
        o = conf["outputs"]
        st.write(f"Sources: {', '.join(o.get('sources', []))}")
        show_png(run, (o["files"].get("png") or [None])[-1])
        show_png(run, (o["files"].get("png") or [None])[0], "whole area")
        st.dataframe(pd.DataFrame([o.get("affected_by_confidence_km2", {})]), hide_index=True)
        download(run, o["files"].get("polygons"), "Affected areas (GeoJSON)", "application/json")
    else:
        missing(res, "confidence")

with tabs[2]:
    if dmg:
        o = dmg["outputs"]
        show_png(run, (o["files"].get("png") or [None])[-1])
        st.subheader("Most affected settlements")
        st.dataframe(pd.DataFrame(o["settlements"]["most_affected"]), hide_index=True,
                     width="stretch")
        st.subheader("Bridges potentially affected")
        st.dataframe(pd.DataFrame(o["bridges"]["list"]), hide_index=True,
                     width="stretch")
        for key, label in (("buildings", "Buildings"), ("roads", "Roads"), ("bridges", "Bridges"),
                           ("facilities", "Health facilities")):
            download(run, o["files"].get(key), f"{label} (GeoJSON, ODbL)", "application/json")
    else:
        missing(res, "damage")

with tabs[3]:
    if cut:
        o = cut["outputs"]
        st.caption(o["meaning"]["likely cut off"])
        show_png(run, (o["files"].get("png") or [None])[-1])
        st.subheader("Settlements likely cut off by road")
        st.dataframe(pd.DataFrame([{"settlement": s["name"], "buildings": s["buildings"],
                                    "on foot": s["on_foot"], "situation": s["sentence"]}
                                   for s in o["cut_off_settlements"]]),
                     hide_index=True, width="stretch")
        st.subheader("Key breaks (repairing these reconnects the most buildings)")
        st.dataframe(pd.DataFrame(o["key_breaks"]), hide_index=True, width="stretch")
        download(run, o["files"].get("settlements"), "All settlements (GeoJSON)",
                 "application/json")
    else:
        missing(res, "cutoff")

with tabs[4]:
    pri = stage(res, "priority")
    if pri:
        st.caption(pri["outputs"]["method"])
        csv_rel = pri["outputs"]["files"].get("csv")
        if csv_rel and (run / csv_rel).is_file():
            st.dataframe(pd.read_csv(run / csv_rel), hide_index=True, width="stretch")
        download(run, csv_rel, "Download priority list (CSV)", "text/csv")
    else:
        missing(res, "priority")

with tabs[5]:
    lz = stage(res, "landing_zones")
    if lz:
        o = lz["outputs"]
        st.warning(o.get("status_wording", ""))
        st.write(f"Candidates for **{o.get('settlements_served', 0)} of "
                 f"{o.get('likely_cut_off_settlements', 0)}** settlements likely cut off by road.")
        show_png(run, (o.get("files", {}).get("png") or [None])[0])
        if o.get("options"):
            st.dataframe(pd.DataFrame(o["options"]).drop(columns=["status"], errors="ignore"),
                         hide_index=True, width="stretch")
        download(run, o.get("files", {}).get("geojson"), "Landing-zone candidates (GeoJSON)",
                 "application/json")
    else:
        missing(res, "landing_zones")

with tabs[6]:
    md = stage(res, "multidate")
    if md:
        o = md["outputs"]
        st.caption(o["method"])
        st.dataframe(pd.DataFrame(o["timeline"]), hide_index=True, width="stretch")
        st.write(o["changes_first_to_last"])
        show_png(run, (o["files"].get("png") or [None])[0])
        if o.get("possible_lakes"):
            st.warning("Growing water bodies flagged as POSSIBLE lakes forming (e.g. behind "
                       "debris): verify.")
            st.dataframe(pd.DataFrame(o["possible_lakes"]), hide_index=True, width="stretch")
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

with tabs[7]:
    fp = stage(res, "flood_path")
    if fp:
        o = fp["outputs"]
        st.write(f"Traced {o['length_km']} km downstream, elevation drop "
                 f"{o['elevation_start_m'] - o['elevation_end_m']:.0f} m.")
        show_png(run, (o["files"].get("png") or [None])[0])
        if o.get("settlements_along_path"):
            st.dataframe(pd.DataFrame(o["settlements_along_path"]), hide_index=True,
                         width="stretch")
    else:
        missing(res, "flood_path")
        st.caption("Set an upstream point in the sidebar ('New analysis') to trace a path.")

with tabs[8]:
    seg = stage(res, "segmentation")
    if seg:
        o = seg["outputs"]
        st.write(f"**{o['model']}**: our own model, trained from scratch on Sen1Floods11, "
                 "running locally. " + o["note"] + ".")
        show_png(run, (o["files"].get("png") or [None])[0])
        scores = o.get("model_test_scores") or {}
        st.dataframe(pd.DataFrame([{"data": k, **(v or {})} for k, v in scores.items()]),
                     hide_index=True)
        st.caption(o.get("domain_shift_note", ""))
    else:
        missing(res, "segmentation")

with tabs[9]:
    st.subheader("Data used (allowed inputs only)")
    for name in ("s1", "s2", "terrain", "osm"):
        s = res.get("stages", {}).get(name)
        if s:
            st.write(f"**{name}** ({s['status']}): {s.get('message', '')}")
    st.subheader("What this system cannot do")
    from floodmap.sitrep import LIMITATIONS

    for line in LIMITATIONS:
        st.write("- " + line)
    st.subheader("Attributions")
    for line in attribution.DATA_ATTRIBUTIONS:
        st.write("- " + line)
    if stage(res, "segmentation"):
        st.write("- Training data: " + attribution.SEN1FLOODS11_CITATION)

footer(res)
