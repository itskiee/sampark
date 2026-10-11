from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from shapely.geometry import box

from floodmap.catalogue import S1Scene
from floodmap.s1_pairing import NoPairError, merge_slices, rank_pairs

AOI = box(85.28, 28.10, 85.42, 28.30)
EPSG = 32645
EVENT = date(2026, 8, 26)
FULL = box(84.5, 27.5, 86.5, 29.0)  # footprint covering the whole AOI
KW = dict(pre_window_days=36, post_window_days=24, max_pre_images=3, min_coverage=0.5,
          include_event_day_as_post=False, same_pass_minutes=10)


def scene(day: str, orbit: int, state: str, hhmm: str = "00:18", footprint=FULL,
          pols=("VV", "VH"), mode="IW", platform="sentinel-1d", sid=None) -> S1Scene:
    when = datetime.fromisoformat(f"{day}T{hhmm}:00+00:00")
    return S1Scene(sid or f"{orbit}-{day}-{hhmm}", when, platform, orbit, state, mode,
                   tuple(pols), footprint)


def series(orbit, state, first_day, n, hhmm="00:18", step=12, **kw):
    d0 = date.fromisoformat(first_day)
    return [scene((d0 + timedelta(days=step * i)).isoformat(), orbit, state, hhmm, **kw)
            for i in range(n)]


def test_trishuli_like_inventory_picks_closest_post_image():
    scenes = (series(19, "descending", "2026-07-19", 7)
              + series(85, "ascending", "2026-07-23", 6, hhmm="12:21")
              + series(121, "descending", "2026-07-26", 6, hhmm="00:10"))
    pairs = rank_pairs(scenes, EVENT, AOI, EPSG, **KW)
    best = pairs[0]
    assert (best.relative_orbit, best.orbit_state) == (85, "ascending")
    assert best.post.day == date(2026, 8, 28) and best.post_delay_days == 2
    assert [p.day for p in best.pre] == [date(2026, 8, 16), date(2026, 8, 4), date(2026, 7, 23)]
    assert best.gap_days == 12
    assert [p.relative_orbit for p in pairs] == [85, 121, 19]  # 2, 5, 10 days after


def test_images_from_different_tracks_are_never_mixed():
    scenes = [scene("2026-08-20", 19, "descending"), scene("2026-08-28", 85, "ascending")]
    with pytest.raises(NoPairError, match="No orbit track"):
        rank_pairs(scenes, EVENT, AOI, EPSG, **KW)


def test_same_pass_slices_are_merged():
    slices = [scene("2026-08-28", 85, "ascending", "12:21", sid="a"),
              scene("2026-08-28", 85, "ascending", "12:21", sid="b")]
    slices[1] = S1Scene("b", slices[0].acquired + timedelta(seconds=25), "sentinel-1d", 85,
                        "ascending", "IW", ("VV", "VH"), FULL)
    acqs = merge_slices(slices, same_pass_minutes=10)
    assert len(acqs) == 1 and acqs[0].scene_ids == ("a", "b")


def test_event_day_image_excluded_by_default_and_optional():
    scenes = [scene("2026-08-14", 85, "ascending"), scene("2026-08-26", 85, "ascending"),
              scene("2026-09-07", 85, "ascending")]
    default = rank_pairs(scenes, EVENT, AOI, EPSG, **KW)[0]
    assert default.post.day == date(2026, 9, 7)
    assert all(p.day != EVENT for p in default.pre)  # never used as "before" either
    allowed = rank_pairs(scenes, EVENT, AOI, EPSG, **{**KW, "include_event_day_as_post": True})[0]
    assert allowed.post.day == EVENT


def test_no_post_image_gives_clear_error():
    scenes = series(85, "ascending", "2026-07-23", 3)  # all before the event
    with pytest.raises(NoPairError, match="post-event"):
        rank_pairs(scenes, EVENT, AOI, EPSG, **KW)


def test_no_scenes_gives_clear_error():
    with pytest.raises(NoPairError, match="No Sentinel-1"):
        rank_pairs([], EVENT, AOI, EPSG, **KW)


def test_low_coverage_images_are_dropped_and_coverage_ranks_first():
    sliver = box(85.41, 28.10, 85.50, 28.30)  # covers ~7% of the AOI
    scenes = ([scene("2026-08-24", 19, "descending", footprint=sliver),
               scene("2026-08-27", 19, "descending", footprint=sliver)]
              + [scene("2026-08-16", 85, "ascending"), scene("2026-09-09", 85, "ascending")])
    pairs = rank_pairs(scenes, EVENT, AOI, EPSG, **KW)
    assert [p.relative_orbit for p in pairs] == [85]
    assert pairs[0].min_coverage == pytest.approx(1.0)


def test_mixed_platforms_on_same_track_are_paired():
    scenes = [scene("2026-08-22", 85, "ascending", platform="sentinel-1a"),
              scene("2026-08-28", 85, "ascending", platform="sentinel-1d")]
    pair = rank_pairs(scenes, EVENT, AOI, EPSG, **KW)[0]
    assert pair.pre[0].platforms == ("sentinel-1a",) and pair.post.platforms == ("sentinel-1d",)
    assert pair.gap_days == 6  # 6-day pairs are fine: the rule is same track, not 12 days


def test_vv_only_images_are_flagged():
    scenes = [scene("2026-08-16", 85, "ascending", pols=("VV",)),
              scene("2026-08-28", 85, "ascending")]
    pair = rank_pairs(scenes, EVENT, AOI, EPSG, **KW)[0]
    assert pair.polarizations == ("VV",)
    assert any("VV only" in n for n in pair.notes)


def test_non_iw_and_hh_scenes_are_ignored():
    scenes = [scene("2026-08-16", 85, "ascending", mode="EW"),
              scene("2026-08-28", 85, "ascending", pols=("HH", "HV"))]
    with pytest.raises(NoPairError):
        rank_pairs(scenes, EVENT, AOI, EPSG, **KW)


def test_window_limits():
    scenes = [scene("2026-07-01", 85, "ascending"),  # 56 days before: outside window
              scene("2026-08-28", 85, "ascending")]
    with pytest.raises(NoPairError):
        rank_pairs(scenes, EVENT, AOI, EPSG, **KW)


def test_describe_is_json_ready():
    scenes = [scene("2026-08-16", 85, "ascending"), scene("2026-08-28", 85, "ascending")]
    info = rank_pairs(scenes, EVENT, AOI, EPSG, **KW)[0].describe()
    assert info["track"] == "85 ascending" and info["post_delay_days"] == 2
    assert info["post"]["start_utc"].startswith("2026-08-28")
    assert isinstance(datetime.now(UTC), datetime)
