import logging
import os
from fastapi import APIRouter, HTTPException, Query
from services.datasets import get_active_inpx_path
from services.inpx_parser import parse_network, parse_network_root
from services.scenario_project import build_scenario_tree, can_build_scenario_tree

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/network", tags=["network"])

_cache: dict[tuple[float, str, str], dict] = {}


@router.get("")
def get_network(scenario_id: str | None = Query(None)):
    inpx_path = get_active_inpx_path()
    try:
        mtime = os.path.getmtime(inpx_path)
    except OSError:
        raise HTTPException(status_code=404, detail=f"INPX file not found at {inpx_path}")

    cache_key = (mtime, str(inpx_path), scenario_id or "")
    if cache_key in _cache:
        return _cache[cache_key]

    if can_build_scenario_tree(scenario_id):
        logger.info("Parsing network for scenario %s from %s", scenario_id, inpx_path)
        tree = build_scenario_tree(inpx_path, scenario_id)
        result = parse_network_root(tree.getroot())
    else:
        logger.info("Parsing base network from %s", inpx_path)
        result = parse_network(str(inpx_path))

    _cache[cache_key] = result
    return result
