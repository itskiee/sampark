"""Required attributions, citations and the prototype disclaimer.

Single source of truth: every output (results.json, PNG footers, dashboard footer,
situation report) imports these strings, so the wording required by the brief can
never drift between outputs. Text is copied verbatim from the official brief.
"""

SENTINEL = "Contains modified Copernicus Sentinel data 2026."
COPERNICUS_DEM = (
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and "
    "Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; "
    "all rights reserved."
)
OSM = "© OpenStreetMap contributors."

# Cited when the corresponding training dataset is used (segmentation model, M8).
KURO_SIWO_CITATION = (
    "Bountos, N. I. et al. (2024). Kuro Siwo: 33 billion m² under the water. "
    "A global multi-temporal satellite dataset for rapid flood mapping. NeurIPS 2024 "
    "Datasets and Benchmarks."
)
SEN1FLOODS11_CITATION = (
    "Bonafilia, D., Tellman, B., Anderson, T., Issenberg, E. (2020). Sen1Floods11: "
    "a georeferenced dataset to train and test deep learning flood algorithms for "
    "Sentinel-1. CVPR Workshops 2020."
)

# Only for material that uses the EMSR927 comparison (report, validation/ outputs).
EMS_VALIDATION_CREDIT = "European Union, Copernicus Emergency Management Service data."

DATA_ATTRIBUTIONS = (SENTINEL, COPERNICUS_DEM, OSM)

DISCLAIMER = (
    "Educational prototype built for the Multimodal AI Hackathon 2026 — not an operational "
    "tool. All results are estimates from satellite data and pre-event OpenStreetMap and "
    "must be verified on the ground before any decision is made."
)


def footer_text() -> str:
    """Compact footer for maps, PNGs and reports."""
    return " ".join((*DATA_ATTRIBUTIONS, DISCLAIMER))
