"""Pipeline stages. Importing this package registers every implemented stage."""

from floodmap.stages import (  # noqa: F401
    confidence,
    damage,
    flood_path,
    osm,
    s1,
    s2,
    terrain,
)
