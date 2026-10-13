"""Pipeline stages. Importing this package registers every implemented stage."""

from floodmap.stages import (  # noqa: F401
    confidence,
    cutoff,
    damage,
    exports,
    flood_path,
    landing_zones,
    multidate,
    osm,
    priority,
    report,
    s1,
    s2,
    segmentation,
    terrain,
)
