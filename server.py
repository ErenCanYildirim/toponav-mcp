"""TopoNav MCP server: exposes a building map as tools an AI assistant can call.

Run:  python server.py
Map:  set TOPONAV_MAP to a JSON map path, or it loads sample_building.json
      from the same folder as this file.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from graph import VALID_NODE_TYPES, MapError, NodeNotFoundError, NoRouteError, TopoMap
from instructions import describe_route

DEFAULT_MAP = Path(__file__).parent / "sample_building.json"

mcp = FastMCP(
    "TopoNav",
    instructions=(
        "Navigation for a building modeled as a graph of rooms, corridors, stairs, "
        "elevators and exits. Locations can be given by id or display name. "
        "Call list_locations first if unsure what a place is called. "
        "Routes are shortest by distance. Defaults: routes NEVER pass through "
        "locked doors (allow_locked=false) and MAY use stairs (accessible_only=false). "
        "Use avoid, accessible_only and allow_locked for constraints the user mentions. "
        "Every route result states the conditions it was computed under. "
        "When no route exists, the error explains the cause (locked door, stairs, "
        "an avoided location, or a one-way connection); relay that cause to the user. "
        "Some connections are one-way, e.g. emergency exits with no re-entry."
    ),
)

_state: dict = {"map": None, "path": None, "load_error": None}


def _load(path: str | Path) -> TopoMap:
    m = TopoMap.from_file(path)
    _state.update(map=m, path=str(path), load_error=None)
    return m


try:
    _load(os.environ.get("TOPONAV_MAP", DEFAULT_MAP))
except MapError as e:
    _state["load_error"] = str(e)


def _map() -> TopoMap:
    if _state["map"] is None:
        raise ToolError(f"No map loaded ({_state['load_error']}). Call load_map with a valid path.")
    return _state["map"]


@contextmanager
def _errors():
    """Turn map/routing errors into clean messages the assistant can relay."""
    try:
        yield
    except (MapError, NodeNotFoundError, NoRouteError) as e:
        raise ToolError(str(e))


# Reusable parameter types
Location = Annotated[str, Field(description="Location id or display name, e.g. 'lab_102' or 'Robotics Lab'.")]
Avoid = Annotated[Optional[list[str]], Field(description="Locations the route must not pass through.")]
AccessibleOnly = Annotated[bool, Field(description="Only use step-free connections (no stairs). Default false: stairs may be used.")]
AllowLocked = Annotated[bool, Field(description="Allow passing through locked doors. Default false: locked doors are never used.")]


@mcp.tool
def load_map(path: Annotated[str, Field(description="Path to a map JSON file.")]) -> dict:
    """Load a different building map, replacing the current one."""
    with _errors():
        m = _load(path)
    return {"loaded": path, "building": m.meta.get("name"), "locations": m.graph.number_of_nodes()}


@mcp.tool
def list_locations(
    floor: Annotated[Optional[int], Field(description="Only this floor.")] = None,
    location_type: Annotated[Optional[str], Field(description=f"Only this type: {sorted(VALID_NODE_TYPES)}.")] = None,
) -> dict:
    """List locations in the building, optionally filtered by floor or type."""
    m = _map()
    nodes = [
        {"id": n["id"], "name": n["name"], "type": n["type"], "floor": n["floor"]}
        for n in m.all_nodes()
        if (floor is None or n["floor"] == floor)
        and (location_type is None or n["type"] == location_type)
    ]
    return {"building": m.meta.get("name"), "count": len(nodes), "locations": nodes}


@mcp.tool
def get_location(location: Location) -> dict:
    """Details about one location: type, floor, tags."""
    with _errors():
        return _map().node_info(location)


@mcp.tool
def get_neighbors(location: Location, accessible_only: AccessibleOnly = False,
                  allow_locked: AllowLocked = False) -> dict:
    """Locations directly connected to the given one, with distances.

    'neighbors' are places you can move to from here. 'one_way_in' are places with a
    one-way connection into this location that cannot be traveled back.
    """
    with _errors():
        m = _map()
        node_id = m.resolve(location)
        neighbors = [
            {"id": n["id"], "name": n["name"], "distance": n["distance"],
             "accessible": n["accessible"], "locked": n["locked"], "description": n["description"]}
            for n in m.neighbors(node_id, accessible_only, allow_locked)
        ]
        one_way_in = [
            {"id": n["id"], "name": n["name"], "distance": n["distance"], "description": n["description"]}
            for n in m.one_way_incoming(node_id)
        ]
    return {"location": node_id, "neighbors": neighbors, "one_way_in": one_way_in}


@mcp.tool
def find_route(start: Location, end: Location, avoid: Avoid = None,
               accessible_only: AccessibleOnly = False, allow_locked: AllowLocked = False) -> dict:
    """Shortest route between two locations, with step-by-step directions.

    Never uses locked doors unless allow_locked is true. If no route exists, the error
    says why (locked door, stairs, avoided location, or one-way connection).
    """
    with _errors():
        m = _map()
        return describe_route(m, m.shortest_route(start, end, avoid, accessible_only, allow_locked))


@mcp.tool
def find_nearest(start: Location,
                 location_type: Annotated[str, Field(description=f"One of: {sorted(VALID_NODE_TYPES)}.")],
                 avoid: Avoid = None, accessible_only: AccessibleOnly = False,
                 allow_locked: AllowLocked = False) -> dict:
    """Route to the closest location of a type, e.g. the nearest exit or elevator.

    Never uses locked doors unless allow_locked is true.
    """
    if location_type not in VALID_NODE_TYPES:
        raise ToolError(f"Unknown type '{location_type}'. Use one of: {sorted(VALID_NODE_TYPES)}.")
    with _errors():
        m = _map()
        return describe_route(m, m.nearest_of_type(start, location_type, avoid, accessible_only, allow_locked))


@mcp.tool
def reachable_from(location: Location, avoid: Avoid = None,
                   accessible_only: AccessibleOnly = False, allow_locked: AllowLocked = False) -> dict:
    """All locations reachable from a starting point under the given constraints.

    Never passes through locked doors unless allow_locked is true.
    """
    with _errors():
        m = _map()
        start = m.resolve(location)
        ids = m.reachable_from(start, avoid, accessible_only, allow_locked)
    return {
        "start": start,
        "count": len(ids),
        "reachable": [{"id": i, "name": m.graph.nodes[i]["name"]} for i in ids],
    }


if __name__ == "__main__":
    mcp.run()