"""Graph layer for TopoNav: loads a building map from JSON, validates it,
and answers neighbor, reachability, and shortest-route queries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    constraints: dict = field(default_factory=dict)


class TopoMap:
    def __init__(self, data: dict):
        self.meta = data.get("meta", {})
        self.graph = nx.DiGraph()
        self._build(data)

    # ---------- loading & validation ----------

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

    # ---------- lookup ----------

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

    # ---------- constrained view ----------

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

    # ---------- queries ----------

    def _name(self, node_id: str) -> str:
        return self.graph.nodes[node_id]["name"]

    def _edge_label(self, u: str, v: str) -> str:
        return f"{self._name(u)} -> {self._name(v)}"

    def neighbors(self, location: str, accessible_only=False, allow_locked=False) -> list[dict]:
        """Locations you can move to directly from this one."""
        node_id = self.resolve(location)
        view = self._view(accessible_only=accessible_only, allow_locked=allow_locked)
        return [
            {"id": n, "name": self._name(n), **self.graph.edges[node_id, n]}
            for n in view.successors(node_id)
        ]

    def one_way_incoming(self, location: str) -> list[dict]:
        """Locations with a one-way connection INTO this one (no way back)."""
        node_id = self.resolve(location)
        return [
            {"id": p, "name": self._name(p), **self.graph.edges[p, node_id]}
            for p in self.graph.predecessors(node_id)
            if not self.graph.has_edge(node_id, p)
        ]

    def reachable_from(self, location: str, avoid=None, accessible_only=False,
                       allow_locked=False) -> list[str]:
        start = self.resolve(location)
        view = self._view(avoid, accessible_only, allow_locked, keep=(start,))
        return sorted(nx.descendants(view, start))

    @staticmethod
    def _constraints(avoid, accessible_only, allow_locked) -> dict:
        return {"avoid": list(avoid or []), "accessible_only": accessible_only,
                "allow_locked": allow_locked}

    def _explain(self, search, avoid, accessible_only, allow_locked) -> list[str]:
        """Find which active constraint blocks a search, by relaxing one at a time.

        `search(avoid, accessible_only, allow_locked)` returns a path or None.
        """
        reasons = []
        if not allow_locked:
            path = search(avoid, accessible_only, True)
            if path:
                locked = [self._edge_label(u, v) for u, v in zip(path, path[1:])
                          if self.graph.edges[u, v]["locked"]]
                restricted = [self._name(n) for n in path
                              if "restricted" in self.graph.nodes[n].get("tags", [])]
                msg = f"only reachable through a locked door ({', '.join(locked)})"
                if restricted:
                    msg += f"; restricted area: {', '.join(restricted)}"
                reasons.append(msg)
        if accessible_only:
            path = search(avoid, False, allow_locked)
            if path:
                stairs = [self._edge_label(u, v) for u, v in zip(path, path[1:])
                          if not self.graph.edges[u, v]["accessible"]]
                reasons.append(f"no step-free route; requires stairs ({', '.join(stairs)})")
        if avoid:
            path = search(None, accessible_only, allow_locked)
            if path:
                avoided = {self.resolve(a) for a in avoid}
                hit = [self._name(n) for n in path if n in avoided]
                reasons.append(f"requires passing through {', '.join(hit)}")
        if not reasons and (avoid or accessible_only or not allow_locked):
            if search(None, False, True):
                reasons.append("blocked by the combination of constraints; no single one is the cause")
        return reasons

    @staticmethod
    def _format(base: str, reasons: list[str]) -> str:
        if len(reasons) == 1:
            return f"{base}: {reasons[0]}."
        options = "; ".join(f"({i}) {r}" for i, r in enumerate(reasons, 1))
        return f"{base}. More than one condition blocks it; lifting any one would allow a route: {options}."

    def _path_or_none(self, s, t, avoid, accessible_only, allow_locked):
        try:
            view = self._view(avoid, accessible_only, allow_locked, keep=(s, t))
            return nx.dijkstra_path(view, s, t, weight="distance")
        except (nx.NetworkXNoPath, NoRouteError):
            return None

    def shortest_route(self, start: str, end: str, avoid=None, accessible_only=False,
                       allow_locked=False) -> Route:
        s, t = self.resolve(start), self.resolve(end)
        view = self._view(avoid, accessible_only, allow_locked, keep=(s, t))
        try:
            dist, path = nx.single_source_dijkstra(view, s, t, weight="distance")
        except nx.NetworkXNoPath:
            raise NoRouteError(self._no_route_message(s, t, avoid, accessible_only, allow_locked))
        return Route(path, dist, self._constraints(avoid, accessible_only, allow_locked))

    def _no_route_message(self, s, t, avoid, accessible_only, allow_locked) -> str:
        base = f"No route from '{self._name(s)}' to '{self._name(t)}'"
        reasons = self._explain(
            lambda a, acc, lk: self._path_or_none(s, t, a, acc, lk),
            avoid, accessible_only, allow_locked,
        )
        if reasons:
            return self._format(base, reasons)

        # Not reachable even with every constraint lifted: check for one-way connections.
        if self.graph.out_degree(s) == 0:
            entries = [self._name(p) for p in self.graph.predecessors(s)]
            return (f"{base}: '{self._name(s)}' is one-way (entry only, e.g. an exit with no "
                    f"re-entry). It can only be reached from: {', '.join(entries) or 'nowhere'}.")
        back = self._path_or_none(t, s, None, False, True)
        if back:
            one_way = [self._edge_label(u, v) for u, v in zip(back, back[1:])
                       if not self.graph.has_edge(v, u)]
            return (f"{base}: the connection is one-way. You can go from '{self._name(t)}' to "
                    f"'{self._name(s)}' but not back (one-way: {', '.join(one_way)}).")
        return f"{base}: the two locations are not connected in the map."

    def nearest_of_type(self, start: str, node_type: str, avoid=None,
                        accessible_only=False, allow_locked=False) -> Route:
        """Shortest route to the closest node of a given type, e.g. the nearest exit."""
        s = self.resolve(start)

        def search(a, acc, lk):
            view = self._view(a, acc, lk, keep=(s,))
            dists, paths = nx.single_source_dijkstra(view, s, weight="distance")
            found = [(d, n) for n, d in dists.items()
                     if n != s and self.graph.nodes[n]["type"] == node_type]
            return paths[min(found)[1]] if found else None

        view = self._view(avoid, accessible_only, allow_locked, keep=(s,))
        dists, paths = nx.single_source_dijkstra(view, s, weight="distance")
        candidates = [(d, n) for n, d in dists.items()
                      if n != s and self.graph.nodes[n]["type"] == node_type]
        if not candidates:
            base = f"No reachable '{node_type}' from '{self._name(s)}'"
            reasons = self._explain(search, avoid, accessible_only, allow_locked)
            raise NoRouteError(self._format(base, reasons) if reasons else f"{base}.")
        d, n = min(candidates)
        return Route(paths[n], d, self._constraints(avoid, accessible_only, allow_locked))