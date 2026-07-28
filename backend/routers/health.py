"""GET /api/health-check — "모델 건강검진" (model health-check), a one-click
integrity report over the ACTIVE dataset (see services.datasets.get_active_
inpx_path — the same source routers/network.py and routers/geojson.py read),
built entirely by services.agent.health_check.HealthCheckRunner: no COM, no
live Vissim, just a parse of whichever .inpx is currently active.

Runs synchronously — measured at well under a second against both the
bundled demo network and the real Urban Intersection Beijing.CN example (see
the health-check unit's own report), so no job/poll machinery is needed
today. If a future, much larger network ever pushes this past ~3s, this is
the seam to convert to a small background job (docs/phase2/00-EXECUTION-
GUIDE.md's job-queue discipline), not a reason to add COM here — this
endpoint never touches Vissim.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response

from services.agent.health_check import HealthCheckRunner
from services.datasets import get_active_dataset, get_active_inpx_path

router = APIRouter(prefix="/api/health-check", tags=["health-check"])

_runner = HealthCheckRunner()


def _build_report() -> dict:
    inpx_path = get_active_inpx_path()
    if not Path(inpx_path).is_file():
        raise HTTPException(status_code=404, detail=f"Active dataset .inpx not found at {inpx_path}")
    dataset_meta = get_active_dataset()
    try:
        return _runner.run(inpx_path, dataset_meta=dataset_meta)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Health check failed: {exc}") from exc


@router.get("")
def run_health_check(format: str | None = Query(None, description="Set to 'json' for an attachment download")):
    report = _build_report()
    if format == "json":
        filename = "vissim_health_check.json"
        return Response(
            content=json.dumps(report, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return report
