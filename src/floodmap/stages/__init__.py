"""Pipeline stages. Importing this package registers every implemented stage."""

from floodmap.stages import (  # noqa: F401
    confidence,
    cutoff,
    damage,
    flood_path,
    osm,
    priority,
    s1,
    s2,
    terrain,
)
