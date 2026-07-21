from fastapi import APIRouter, HTTPException, Query
from services.results_reader import list_scenarios, get_scenario_results, get_compare, get_compare_summary

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


@router.get("")
def scenarios():
    return list_scenarios()


@router.get("/compare")
def compare(ids: str = Query(..., description="Comma-separated scenario IDs")):
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="At least one scenario ID is required")
    return get_compare(id_list)


@router.get("/compare-summary")
def compare_summary(ids: str = Query(..., description="Comma-separated scenario IDs")):
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="At least one scenario ID is required")
    return get_compare_summary(id_list)


@router.get("/{scenario_id}/results")
def scenario_results(scenario_id: str):
    result = get_scenario_results(scenario_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found or has no results")
    return result
