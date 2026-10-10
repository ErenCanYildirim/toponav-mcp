"""Tests for TopoNav. Run from the repo root with:  pytest"""

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph import MapError, NodeNotFoundError, NoRouteError, TopoMap  # noqa: E402
from instructions import describe_route  # noqa: E402

MAP_PATH = ROOT / "sample_building.json"


@pytest.fixture(scope="module")
def raw():
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def m():
    return TopoMap.from_file(MAP_PATH)


# ---------- loading & validation ----------

def test_sample_map_loads(m):
    assert m.graph.number_of_nodes() == 18
    assert m.meta["distance_unit"] == "meters"


def test_missing_file_raises():
    with pytest.raises(MapError, match="not found"):
        TopoMap.from_file(ROOT / "does_not_exist.json")


def test_invalid_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(MapError, match="not valid JSON"):
        TopoMap.from_file(bad)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["nodes"].append(dict(d["nodes"][0])), "Duplicate node id"),
    (lambda d: d["nodes"][0].update(type="balcony"), "invalid type"),
    (lambda d: d["edges"].append({"source": "lobby", "target": "ghost", "distance": 2}), "unknown node 'ghost'"),
    (lambda d: d["edges"][0].update(distance=-1), "positive 'distance'"),
    (lambda d: d["edges"].append({"source": "lobby", "target": "lobby", "distance": 1}), "to itself"),
    (lambda d: d.pop("edges"), "'nodes' and 'edges'"),
])
def test_validation_errors(raw, mutate, message):
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(MapError, match=message):
        TopoMap(data)


def test_resolve_accepts_id_and_name(m):
    assert m.resolve("lab_102") == "lab_102"
    assert m.resolve("robotics lab") == "lab_102"
    with pytest.raises(NodeNotFoundError):
        m.resolve("Cafeteria")


# ---------- routing scenarios (the manual test questions) ----------

def test_basic_route(m):
    r = m.shortest_route("Robotics Lab", "Main Entrance")
    assert r.nodes == ["lab_102", "corridor_1b", "corridor_1a", "lobby", "entrance_main"]
    assert r.distance == 23


def test_nearest_exit(m):
    r = m.nearest_of_type("Robotics Lab", "exit")
    assert r.nodes[-1] == "exit_rear" and r.distance == 8


def test_nearest_exit_avoiding_kitchen(m):
    r = m.nearest_of_type("Robotics Lab", "exit", avoid=["Kitchen"])
    assert r.nodes[-1] == "exit_emergency" and r.distance == 11
    assert "kitchen" not in r.nodes


def test_accessible_route_uses_elevator(m):
    default = m.shortest_route("Office 101", "Library")
    accessible = m.shortest_route("Office 101", "Library", accessible_only=True)
    assert default.distance == 24 and "stairwell_1" in default.nodes
    assert accessible.distance == 26 and "elevator_1" in accessible.nodes
    assert "stairwell_1" not in accessible.nodes


def test_reachable_avoiding_west_corridor(m):
    default = m.reachable_from("Lobby", avoid=["West Corridor"])
    accessible = m.reachable_from("Lobby", avoid=["West Corridor"], accessible_only=True)
    assert len(default) == 8 and "stairwell_1" in default
    assert len(accessible) == 7 and "stairwell_1" not in accessible


def test_locked_room_explained(m):
    with pytest.raises(NoRouteError, match="locked door") as e:
        m.shortest_route("Lobby", "Server Room")
    assert "restricted area" in str(e.value)
    assert m.shortest_route("Lobby", "Server Room", allow_locked=True).nodes[-1] == "server_room"


def test_one_way_exit_explained(m):
    with pytest.raises(NoRouteError, match="one-way"):
        m.shortest_route("Emergency Exit", "Robotics Lab")


def test_stairs_only_explained(m):
    with pytest.raises(NoRouteError, match="requires stairs"):
        m.shortest_route("Office 101", "Library", accessible_only=True, avoid=["elevator_1"])


def test_avoided_location_explained(m):
    with pytest.raises(NoRouteError, match="requires passing through West Corridor"):
        m.shortest_route("Office 101", "Restroom (Floor 1)", avoid=["West Corridor"])


def test_multiple_causes_listed(m):
    with pytest.raises(NoRouteError, match="lifting any one"):
        m.shortest_route("Office 101", "Library", accessible_only=True, avoid=["elevator_1"])


def test_endpoint_in_avoid_list_rejected(m):
    with pytest.raises(NoRouteError, match="endpoint"):
        m.shortest_route("Lobby", "Kitchen", avoid=["Kitchen"])


def test_one_way_incoming_visible(m):
    assert m.neighbors("Emergency Exit") == []
    incoming = m.one_way_incoming("Emergency Exit")
    assert [n["id"] for n in incoming] == ["corridor_1b"]


# ---------- directions ----------

def test_step_distances_add_up_to_total(m):
    for start, end, acc in [("Office 101", "Library", True), ("Library", "Office 101", False),
                            ("Robotics Lab", "Main Entrance", False)]:
        d = describe_route(m, m.shortest_route(start, end, accessible_only=acc))
        assert sum(s["distance"] for s in d["steps"]) == d["total_distance"]


def test_directions_text(m):
    d = describe_route(m, m.shortest_route("Office 101", "Library", accessible_only=True))
    assert "Take the elevator up to floor 2 — 5 m." in d["text"]
    assert "You have arrived at the Library." in d["text"]
    assert "step-free only" in d["summary"] and "no locked doors" in d["summary"]


def test_stairs_use_reported(m):
    d = describe_route(m, m.shortest_route("Office 101", "Library"))
    assert d["uses_stairs"] is True
    assert "uses stairs" in d["summary"]


# ---------- MCP server layer ----------

def _call(tool, args):
    from fastmcp import Client
    from server import mcp

    async def run():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args)).data
    return asyncio.run(run())


def test_server_exposes_expected_tools():
    from fastmcp import Client
    from server import mcp

    async def run():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}
    assert asyncio.run(run()) == {
        "load_map", "list_locations", "get_location", "get_neighbors",
        "find_route", "find_nearest", "reachable_from",
    }


def test_server_find_route():
    data = _call("find_route", {"start": "Robotics Lab", "end": "Main Entrance"})
    assert data["total_distance"] == 23 and data["unit"] == "meters"


def test_server_relays_cause_of_failure():
    with pytest.raises(Exception, match="one-way"):
        _call("find_route", {"start": "Emergency Exit", "end": "Robotics Lab"})


def test_server_rejects_unknown_type():
    with pytest.raises(Exception, match="Unknown type"):
        _call("find_nearest", {"start": "Lobby", "location_type": "bathroom"})