"""Graph layer for TopoNav: loads a building map from JSON, validates it,
and answers neighbor, reachability, and shortest-route queries."""
from __future__ import annotations
 
import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
 
VALID_NODE_TYPES = {"room", "corridor", "exit", "stairs", "elevator"}
FALLBACK_DEFAULTS = {"bidirectional": True, "accessible": True, "locked": False}
 
class MapError(Exception):
    """The map file is missing, malformed, or inconsistent."""
 
 
class NodeNotFoundError(Exception):
    """A requested location does not exist in the map."""
 
 
class NoRouteError(Exception):
    """No route exists under the given constraints."""

@dataclass 
class Route:
    nodes: list[str]
    distance: float 

class TopoMap:
    def __init__(self, data: dict):
        self.meta = data.get("meta", {})
        self.graph = nx.DiGraph()
        self._build(data)

    @classmethod
    def from_file(cls, path: str | Path) -> "TopoMap":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise MapError(f"Map file not found: {path}")
        except json.JSONDecodeError as e:
            raise MapError(f"Map file is not valid JSON: {e}")
        return cls(data)

    def _build(self, data: dict) -> None:
        if not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges"), list):
            raise MapError("Map must contain 'nodes' and 'edges' lists.")

        defaults = {**FALLBACK_DEFAULTS, **self.meta.get("defaults", {})}

        for i, node in enumerate(data["nodes"]):
            node_id = node.get("id")
            if not node_id:
                raise MapError(f"Node #{i} has no 'id'.")
            if node_id in self.graph:
                raise MapError(f"Duplicate node id: '{node_id}'.")
            node_type = node.get("type")
            if node_type not in VALID_NODE_TYPES:
                raise MapError(
                    f"Node '{node_id}' has invalid type '{node_type}'. "
                    f"Expected one of: {sorted(VALID_NODE_TYPES)}."
                )
            self.graph.add_node(
                node_id,
                name=node.get("name", node_id),
                type=node_type,
                floor=node.get("floor"),
                tags=node.get("tags", []),
            )

        for i, edge in enumerate(data["edges"]):
            src, dst = edge.get("source"), edge.get("target")
            for end in (src, dst):
                if end not in self.graph:
                    raise MapError(f"Edge #{i} references unknown node '{end}'.")
            if src == dst:
                raise MapError(f"Edge #{i} connects '{src}' to itself.")
            distance = edge.get("distance")
            if not isinstance(distance, (int, float)) or distance <= 0:
                raise MapError(f"Edge #{i} ({src} -> {dst}) needs a positive 'distance'.")

            attrs = {
                "distance": distance,
                "accessible": edge.get("accessible", defaults["accessible"]),
                "locked": edge.get("locked", defaults["locked"]),
                "description": edge.get("description"),
            }
            self.graph.add_edge(src, dst, **attrs)
            if edge.get("bidirectional", defaults["bidirectional"]):
                self.graph.add_edge(dst, src, **attrs)

    def resolve(self, name_or_id: str) -> str:
        """Accept a node id or a display name (case-insensitive) and return the id."""
        if name_or_id in self.graph:
            return name_or_id
        wanted = name_or_id.strip().lower()
        matches = [n for n, d in self.graph.nodes(data=True) if d["name"].lower() == wanted]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise NodeNotFoundError(f"'{name_or_id}' is ambiguous: {matches}.")
        raise NodeNotFoundError(f"No location called '{name_or_id}'.")

    def node_info(self, node_id: str) -> dict:
        node_id = self.resolve(node_id)
        return {"id": node_id, **self.graph.nodes[node_id]}
 
    def all_nodes(self) -> list[dict]:
        return [{"id": n, **d} for n, d in self.graph.nodes(data=True)]
 
    def _view(self, avoid=None, accessible_only=False, allow_locked=False,
              keep: tuple[str, ...] = ()) -> nx.DiGraph:
        avoid_ids = {self.resolve(a) for a in (avoid or [])}
        for k in keep:
            if k in avoid_ids:
                raise NoRouteError(f"'{self.graph.nodes[k]['name']}' is both an endpoint and in the avoid list.")

        def node_ok(n):
            return n not in avoid_ids
 
        def edge_ok(u, v):
            e = self.graph.edges[u, v]
            if accessible_only and not e["accessible"]:
                return False
            if not allow_locked and e["locked"]:
                return False
            return True

        return nx.subgraph_view(self.graph, filter_node=node_ok, filter_edge=edge_ok)

    def neighbors(self, location: str, accessible_only=False, allow_locked=False) -> list[dict]:
        node_id = self.resolve(location)
        view = self._view(accessible_only=accessible_only, allow_locked=allow_locked)
        return [
            {"id": n, "name": self.graph.nodes[n]["name"], **self.graph.edges[node_id, n]}
            for n in view.successors(node_id)
        ]
 
    def reachable_from(self, location: str, avoid=None, accessible_only=False,
                       allow_locked=False) -> list[str]:
        start = self.resolve(location)
        view = self._view(avoid, accessible_only, allow_locked, keep=(start,))
        return sorted(nx.descendants(view, start))

    def shortest_route(self, start: str, end: str, avoid=None, accessible_only=False,
                       allow_locked=False) -> Route:
        s, t = self.resolve(start), self.resolve(end)
        view = self._view(avoid, accessible_only, allow_locked, keep=(s, t))
        try:
            dist, path = nx.single_source_dijkstra(view, s, t, weight="distance")
        except nx.NetworkXNoPath:
            raise NoRouteError(
                f"No route from '{self.graph.nodes[s]['name']}' to "
                f"'{self.graph.nodes[t]['name']}' with the given constraints."
            )
        return Route(nodes=path, distance=dist)
 
    def nearest_of_type(self, start: str, node_type: str, avoid=None,
                        accessible_only=False, allow_locked=False) -> Route:
        """Shortest route to the closest node of a given type, e.g. the nearest exit."""
        s = self.resolve(start)
        view = self._view(avoid, accessible_only, allow_locked, keep=(s,))
        dists, paths = nx.single_source_dijkstra(view, s, weight="distance")
        candidates = [(d, n) for n, d in dists.items()
                      if n != s and self.graph.nodes[n]["type"] == node_type]
        if not candidates:
            raise NoRouteError(f"No reachable '{node_type}' from '{self.graph.nodes[s]['name']}'.")
        d, n = min(candidates)
        return Route(nodes=paths[n], distance=d)
