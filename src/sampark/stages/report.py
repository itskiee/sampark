"""Stage "report": the one-page situation report (brief "OUTPUT"), from results.json only.

Runs after every other stage it can use; a stage that is missing or failed appears in the
report as "not available" with its reason, so the report is always produced.
"""

from __future__ import annotations

import json

from sampark.pipeline import StageContext, register_stage
from sampark.results import StageResult, StageStatus
from sampark.sitrep import build


@register_stage("report", uses=("s1", "terrain", "s2", "confidence", "segmentation", "osm",
                                "flood_path", "damage", "cutoff", "priority",
                                "landing_zones", "multidate"))
def run_report(ctx: StageContext) -> StageResult:
    # the same dictionary that results.json holds: the report cannot see anything else
    data = json.loads(json.dumps(ctx.results.to_dict(), default=str))
    files = build(data, ctx.results.run_dir, ctx.results.run_dir / "report")
    return StageResult("report", StageStatus.OK,
                       "one-page situation report (PDF, PNG, plain text)",
                       outputs={"files": files})
