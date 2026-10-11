"""Static scanner that enforces the hackathon data rules on pipeline source code.

It parses every .py file under src/, app/ and scripts/ with Python's `ast` module (so it
sees real imports, names and string values, not comments) and reports:

  validation-isolation  pipeline code imports or references the validation/ folder
                        (EMSR927 reference data may only be used there, for checking)
  osmnx                 osmnx is imported or an osmnx download function is referenced
                        (osmnx downloads return CURRENT, post-event OSM)
  overpass              anything mentioning Overpass (also current OSM)
  forbidden-source      identifiers of datasets that are not allowed as inputs
  earth-engine          Google Earth Engine / geemap imported, or the openEO federation
                        endpoint (data must come from the source named in the brief,
                        the Copernicus Data Space Ecosystem)
  collection            openEO load_collection() with a literal collection id that is not
                        in floodmap.data_rules.ALLOWED_OPENEO_COLLECTIONS
  http-library          an HTTP/socket library imported outside floodmap/net.py
                        (all network access must go through the allowlisted client)
  url                   a URL whose host has not been reviewed (REVIEWED_URL_HOSTS)

The denylist lives here, in tests/, deliberately: pipeline code never needs to contain
the names of forbidden datasets, not even to forbid them.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from floodmap.data_rules import ALLOWED_OPENEO_COLLECTIONS

SCANNED_DIRS = ("src", "app", "scripts")

# Lower-case substrings that must not appear in any string or identifier of pipeline code.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    # published damage / emergency maps (validation only)
    "emsr", "unosat", "mapping.emergency.copernicus", "rapidmapping",
    # current OSM sources
    "overpass",
    # surface water / hydrography products
    "jrc/gsw", "globalsurfacewater", "merit/", "merit_hydro", "hydrosheds", "global_hand",
    # other elevation models (only Copernicus DEM is allowed)
    "srtmgl1", "nasadem", "cgiar/srtm", "alos/aw3d", "gmted",
    # land cover
    "worldcover", "dynamicworld",
    # population / settlement layers (population proxy must be OSM building counts)
    "worldpop", "landscan", "ghsl", "hrsl", "ciesin/gpw",
    # third-party building footprints (only OSM buildings are allowed)
    "open-buildings", "open_buildings", "microsoft/buildings", "globalmlbuildingfootprints",
    # derived cloud products (ambiguous; asked organizers — use S2 SCL/QA60 meanwhile)
    "cloud_score_plus", "s2_cloud_probability",
    # community catalogs that host many non-allowed datasets
    "projects/sat-io",
)

# Access routes other than the brief's named source (rule "earth-engine").
FORBIDDEN_ROUTES: tuple[str, ...] = ("earthengine", "geemap", "openeofed")
FORBIDDEN_ROUTE_MODULES: tuple[str, ...] = ("ee", "earthengine", "geemap")

OSMNX_DOWNLOAD = re.compile(r"^(graph|features|geometries|pois|footprints)_from_")
VALIDATION_PATH = re.compile(r"(^|[\\/])validation([\\/]|$)", re.IGNORECASE)
URL = re.compile(r"https?://([^/\s\"'<>]+)", re.IGNORECASE)

# Hosts that are allowed to appear in pipeline code after review. Data access is further
# restricted at runtime by floodmap.data_rules.ALLOWED_HTTP_HOSTS.
REVIEWED_URL_HOSTS = frozenset(
    {
        "api.ohsome.org",  # OSM pre-event snapshots (data source)
        "stac.dataspace.copernicus.eu",  # Copernicus Data Space scene catalogue
        "openeo.dataspace.copernicus.eu",  # Copernicus Data Space processing (openEO)
        "dataspace.copernicus.eu",  # account registration link in messages
    }
)

HTTP_MODULES = ("requests", "urllib3", "httpx", "aiohttp", "http.client", "socket",
                "urllib.request")
HTTP_ALLOWED_FILE = "src/floodmap/net.py"


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _module_matches(module: str, targets: tuple[str, ...]) -> bool:
    return any(module == t or module.startswith(t + ".") for t in targets)


def scan_source(source: str, rel_path: str) -> list[Violation]:
    """Scan one file's source code. `rel_path` uses forward slashes, relative to repo root."""
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [Violation(rel_path, exc.lineno or 0, "syntax", str(exc))]

    found: list[Violation] = []

    def add(node: ast.AST, rule: str, detail: str) -> None:
        found.append(Violation(rel_path, getattr(node, "lineno", 0), rule, detail))

    def check_text(node: ast.AST, text: str, kind: str) -> None:
        low = text.lower()
        for bad in FORBIDDEN_SUBSTRINGS:
            if bad in low:
                rule = "overpass" if bad == "overpass" else "forbidden-source"
                add(node, rule, f"{kind} contains '{bad}': {text[:80]!r}")
        for bad in FORBIDDEN_ROUTES:
            if bad in low:
                add(node, "earth-engine", f"{kind} contains '{bad}': {text[:80]!r}")
        if kind == "identifier" and OSMNX_DOWNLOAD.match(text):
            add(node, "osmnx", f"osmnx-style download function '{text}'")
        if kind == "string":
            if VALIDATION_PATH.search(text) or low.strip() == "validation":
                add(node, "validation-isolation", f"reference to validation folder: {text[:80]!r}")
            for host in URL.findall(text):
                host = host.lower().split(":")[0]
                if host not in REVIEWED_URL_HOSTS:
                    add(node, "url", f"unreviewed URL host '{host}'")

    for node in ast.walk(tree):
        # --- imports -------------------------------------------------------------------
        if isinstance(node, ast.Import | ast.ImportFrom):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            else:
                base = node.module or ""
                modules = [base] + [f"{base}.{a.name}" if base else a.name for a in node.names]
            for mod in modules:
                top = mod.split(".")[0]
                if "validation" in mod.split("."):
                    add(node, "validation-isolation", f"imports '{mod}'")
                if top == "osmnx":
                    add(node, "osmnx", f"imports '{mod}' (osmnx is not used in this project)")
                if top in FORBIDDEN_ROUTE_MODULES:
                    add(node, "earth-engine", f"imports '{mod}' (data comes from Copernicus "
                                              "Data Space only)")
                if _module_matches(mod, HTTP_MODULES) and rel_path != HTTP_ALLOWED_FILE:
                    add(node, "http-library", f"imports '{mod}' outside {HTTP_ALLOWED_FILE}")
                check_text(node, mod, "identifier")

        # --- names / attributes / definitions -----------------------------------------
        elif isinstance(node, ast.Name):
            check_text(node, node.id, "identifier")
        elif isinstance(node, ast.Attribute):
            check_text(node, node.attr, "identifier")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            check_text(node, node.name, "identifier")

        # --- string values (incl. docstrings and f-string parts) ----------------------
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            check_text(node, node.value, "string")

        # --- openEO load_collection with a literal collection id ---------------------
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "load_collection":
                literals = [a for a in node.args if isinstance(a, ast.Constant)
                            and isinstance(a.value, str)]
                literals += [k.value for k in node.keywords if k.arg == "collection_id"
                             and isinstance(k.value, ast.Constant)
                             and isinstance(k.value.value, str)]
                for lit in literals:
                    if lit.value not in ALLOWED_OPENEO_COLLECTIONS:
                        add(node, "collection",
                            f"load_collection('{lit.value}') is not an allowed collection")

    return found


def scan_repo(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for folder in SCANNED_DIRS:
        base = repo_root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            violations.extend(scan_source(path.read_text(encoding="utf-8"), rel))
    return violations


def scanned_file_count(repo_root: Path) -> int:
    return sum(
        len(list((repo_root / d).rglob("*.py"))) for d in SCANNED_DIRS if (repo_root / d).is_dir()
    )
