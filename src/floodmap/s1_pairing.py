"""Choose which Sentinel-1 images to compare: same-track pre/post pairing.

Radar images can only be compared pixel-by-pixel when they were taken from the same
viewing geometry, i.e. the same orbit TRACK (relative orbit number + pass direction).
Different tracks look at the mountains from different angles, so slopes facing the
satellite, shadows and layover all move; comparing them would show "change" everywhere.

This module is pure logic on catalogue metadata (no network), so it is fully unit-tested
offline. Steps:
1. merge the ~25 s slices of one pass into one acquisition per track and time;
2. per track: post = earliest acquisition after the event, pre = latest few before it;
3. drop acquisitions that cover too little of the area;
4. rank tracks: best coverage first, then shortest delay after the event, then shortest
   time gap between images (less natural change between them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import shapely
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

from floodmap.catalogue import S1Scene


@dataclass(frozen=True)
class Acquisition:
    """One satellite pass over the area on one track (one or more merged slices)."""

    relative_orbit: int
    orbit_state: str
    start: datetime
    end: datetime
    platforms: tuple[str, ...]
    polarizations: tuple[str, ...]
    footprint: BaseGeometry
    scene_ids: tuple[str, ...]
    coverage: float = 0.0  # fraction of the AOI covered, 0..1
    incidence_angle: float | None = None  # mean of the slices (degrees), if known

    @property
    def track(self) -> tuple[int, str]:
        return (self.relative_orbit, self.orbit_state)

    @property
    def day(self) -> date:
        return self.start.date()

    @property
    def has_vh(self) -> bool:
        return "VH" in self.polarizations

    def describe(self) -> dict:
        return {
            "start_utc": self.start.isoformat(timespec="minutes"),
            "platforms": list(self.platforms),
            "polarizations": list(self.polarizations),
            "coverage": round(self.coverage, 3),
            "scene_ids": list(self.scene_ids),
        }


@dataclass(frozen=True)
class TrackPair:
    relative_orbit: int
    orbit_state: str
    pre: tuple[Acquisition, ...]  # newest first
    post: Acquisition
    event_date: date
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def min_coverage(self) -> float:
        return min([self.post.coverage, *(p.coverage for p in self.pre)])

    @property
    def post_delay_days(self) -> int:
        return (self.post.day - self.event_date).days

    @property
    def gap_days(self) -> int:
        """Days between the newest pre image and the post image."""
        return (self.post.day - self.pre[0].day).days

    @property
    def polarizations(self) -> tuple[str, ...]:
        """Polarisations present in every image of the pair."""
        common = set(self.post.polarizations)
        for p in self.pre:
            common &= set(p.polarizations)
        return tuple(sorted(common, key=["VV", "VH", "HH", "HV"].index))

    def describe(self) -> dict:
        return {
            "track": f"{self.relative_orbit} {self.orbit_state}",
            "relative_orbit": self.relative_orbit,
            "orbit_state": self.orbit_state,
            "post": self.post.describe(),
            "pre": [p.describe() for p in self.pre],
            "post_delay_days": self.post_delay_days,
            "gap_days": self.gap_days,
            "min_coverage": round(self.min_coverage, 3),
            "polarizations": list(self.polarizations),
            "notes": list(self.notes),
        }


# Sentinel-1A operational phase began in early October 2014 (routine data before: commissioning)
SENTINEL1_START = date(2014, 10, 3)


class NoPairError(RuntimeError):
    """No usable same-track pre/post pair; message is meant for the user."""


def merge_slices(scenes: list[S1Scene], same_pass_minutes: float) -> list[Acquisition]:
    """Group scenes of the same track taken within `same_pass_minutes` into acquisitions."""
    gap = timedelta(minutes=same_pass_minutes)
    acquisitions: list[Acquisition] = []
    by_track: dict[tuple[int, str], list[S1Scene]] = {}
    for s in sorted(scenes, key=lambda s: s.acquired):
        by_track.setdefault(s.track, []).append(s)
    for (orbit, state), items in by_track.items():
        group: list[S1Scene] = [items[0]]
        for s in items[1:]:
            if s.acquired - group[-1].acquired <= gap:
                group.append(s)
            else:
                acquisitions.append(_to_acquisition(orbit, state, group))
                group = [s]
        acquisitions.append(_to_acquisition(orbit, state, group))
    return sorted(acquisitions, key=lambda a: a.start)


def _to_acquisition(orbit: int, state: str, group: list[S1Scene]) -> Acquisition:
    pols = set(group[0].polarizations)
    for s in group[1:]:
        pols &= set(s.polarizations)  # only polarisations every slice has
    angles = [s.incidence_angle for s in group if s.incidence_angle is not None]
    return Acquisition(
        incidence_angle=sum(angles) / len(angles) if angles else None,
        relative_orbit=orbit,
        orbit_state=state,
        start=group[0].acquired,
        end=group[-1].acquired,
        platforms=tuple(sorted({s.platform for s in group})),
        polarizations=tuple(sorted(pols)),
        footprint=shapely.union_all([s.footprint for s in group]),
        scene_ids=tuple(s.scene_id for s in group),
    )


def to_utm(geom: BaseGeometry, utm_epsg: int) -> BaseGeometry:
    """Project a WGS84 lon/lat geometry to UTM metres."""
    project = Transformer.from_crs(4326, utm_epsg, always_xy=True).transform
    return shapely.transform(geom, project, interleaved=False)


def with_coverage(acq: Acquisition, aoi_geom: BaseGeometry, utm_epsg: int) -> Acquisition:
    """Return a copy with `coverage` = share of the AOI area inside the footprint.

    Computed in UTM metres so the ratio is a true area ratio at any latitude.
    """
    aoi_m = to_utm(aoi_geom, utm_epsg)
    covered = to_utm(acq.footprint, utm_epsg).intersection(aoi_m).area
    frac = covered / aoi_m.area if aoi_m.area > 0 else 0.0
    return Acquisition(**{**acq.__dict__, "coverage": min(frac, 1.0)})


def rank_pairs(
    scenes: list[S1Scene],
    event_date: date,
    aoi_geom: BaseGeometry,
    utm_epsg: int,
    *,
    pre_window_days: int,
    post_window_days: int,
    max_pre_images: int,
    min_coverage: float,
    include_event_day_as_post: bool,
    same_pass_minutes: float,
) -> list[TrackPair]:
    """All usable same-track pairs, best first. Raises NoPairError if there are none."""
    usable = [s for s in scenes if s.mode == "IW" and "VV" in s.polarizations]
    if not usable:
        why = ""
        if event_date < SENTINEL1_START:
            why = (f" The flood date {event_date} is before Sentinel-1 began routine "
                   f"observations ({SENTINEL1_START:%B %Y}).")
        elif event_date >= date.today():
            why = f" The flood date {event_date} is today or in the future: no image after it yet."
        raise NoPairError(
            "No Sentinel-1 IW scenes with VV polarisation over this area in the search window."
            + why)
    acqs = [with_coverage(a, aoi_geom, utm_epsg) for a in merge_slices(usable, same_pass_minutes)]
    acqs = [a for a in acqs if a.coverage >= min_coverage]

    first_post_day = event_date if include_event_day_as_post else event_date + timedelta(days=1)
    pre_start = event_date - timedelta(days=pre_window_days)
    post_end = event_date + timedelta(days=post_window_days)

    pairs: list[TrackPair] = []
    for track in sorted({a.track for a in acqs}):
        on_track = [a for a in acqs if a.track == track]
        posts = [a for a in on_track if first_post_day <= a.day <= post_end]
        pres = [a for a in on_track if pre_start <= a.day < event_date]
        if not posts or not pres:
            continue
        post = min(posts, key=lambda a: a.start)
        pre = tuple(sorted(pres, key=lambda a: a.start, reverse=True)[:max_pre_images])
        notes = []
        if len(pre) < max_pre_images:
            notes.append(f"only {len(pre)} pre-event image(s) on this track")
        if not post.has_vh or not all(p.has_vh for p in pre):
            notes.append("VH not available in all images; VV only")
        pairs.append(TrackPair(track[0], track[1], pre, post, event_date, tuple(notes)))

    if not pairs:
        tracks = sorted({a.track for a in acqs})
        raise NoPairError(
            "No orbit track has both a pre-event image (within "
            f"{pre_window_days} days before) and a post-event image (within "
            f"{post_window_days} days after) covering at least {min_coverage:.0%} of the area. "
            f"Tracks with usable coverage: {tracks or 'none'}."
        )
    # Best coverage first (rounded so tiny differences don't dominate), then the image
    # closest after the event, then the shortest time gap between images.
    pairs.sort(key=lambda p: (-round(p.min_coverage, 1), p.post_delay_days, p.gap_days))
    return pairs
