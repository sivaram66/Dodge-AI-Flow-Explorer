from fastapi import APIRouter, HTTPException

from app.services import graph_builder

router = APIRouter(prefix="/graph", tags=["graph"])


def _require_graph() -> dict:
    if graph_builder.graph is None:
        raise HTTPException(status_code=503, detail="Graph not ready")
    return graph_builder.graph


@router.get("/summary")
def get_summary():
    g = _require_graph()
    return g["summary"]


@router.get("/full")
def get_full():
    g = _require_graph()
    return {
        "nodes": list(g["full"]["nodes"].values()),  # react-force-graph expects arrays
        "edges": g["full"]["edges"],
    }


@router.get("/node/{node_id:path}")
def get_node(node_id: str):
    g = _require_graph()
    nodes = g["full"]["nodes"]

    if node_id not in nodes:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    adjacent_edges = [
        e for e in g["full"]["edges"]
        if e["source"] == node_id or e["target"] == node_id
    ]
    neighbor_ids = {
        e["target"] if e["source"] == node_id else e["source"]
        for e in adjacent_edges
    }

    return {
        "node":      nodes[node_id],
        "neighbors": [nodes[nid] for nid in neighbor_ids if nid in nodes],
        "edges":     adjacent_edges,
    }


@router.get("/expand/{entity_type}")
def expand_type(entity_type: str):
    g = _require_graph()
    by_type = g["full"]["by_type"]

    if entity_type not in by_type:
        raise HTTPException(status_code=404, detail=f"Entity type '{entity_type}' not found")

    type_ids = set(by_type[entity_type])
    nodes = g["full"]["nodes"]

    edges = [
        e for e in g["full"]["edges"]
        if e["source"] in type_ids or e["target"] in type_ids
    ]

    return {
        "nodes":          [nodes[nid] for nid in type_ids],
        "edges":          edges,
        "remove_node_id": f"type::{entity_type}",   # summary node to swap out
    }
