"""Turns a Route from graph.py into human-readable, step-by-step directions."""
 
from __future__ import annotations
 
from graph import Route, TopoMap
 
VERTICAL_TYPES = {"stairs", "elevator"}

def _ref(name: str) -> str:
    """'Kitchen' -> 'the Kitchen', but 'Office 101' stays 'Office 101'."""
    base = name.split("(")[0].strip() or name
    last_word = base.split()[-1]
    return name if any(c.isdigit() for c in last_word) else f"the {name}"
 
def _fmt_distance(d: float, unit: str) -> str:
    d = int(d) if float(d).is_integer() else round(d, 1)
    short = {"meters": "m", "feet": "ft"}.get(unit, unit)
    return f"{d} {short}"
 
def _step_text(m: TopoMap, u: str, v: str) -> tuple[str, bool]:
    """Return (sentence, is_vertical) for a single move from u to v."""
    g = m.graph
    src, dst, edge = g.nodes[u], g.nodes[v], g.edges[u, v]
 
    # Changing floors via stairs or elevator
    if src["floor"] != dst["floor"] and src["type"] in VERTICAL_TYPES:
        direction = "up" if (dst["floor"] or 0) > (src["floor"] or 0) else "down"
        mode = "stairs" if src["type"] == "stairs" else "elevator"
        return f"Take the {mode} {direction} to floor {dst['floor']}", True
 
    name = _ref(dst["name"])
    verbs = {
        "corridor": f"Head into {name}",
        "room": f"Enter {name}",
        "exit": f"Leave the building through {name}",
        "stairs": f"Walk to {name}",
        "elevator": f"Walk to {name}",
    }
    text = verbs.get(dst["type"], f"Go to {name}")
    if edge.get("description"):
        text += f" ({edge['description']})"
    return text, False
 
def describe_route(m: TopoMap, route: Route) -> dict:
    """Build structured + plain-text directions for a route"""
    unit = m.meta.get("distance_unit", "meters")
    g = m.graph 
    start, end = route.nodes[0], route.nodes[-1]

    steps = []
    for u, v in zip(route.nodes, route.nodes[1:]):
        text, vertical = _step_text(m, u, v)
        dist = g.edges[u, v]["distance"]
        if not vertical:
            text += f" — {_fmt_distance(dist, unit)}"
        steps.append({"from": u, "to": v, "distance": dist, "text": text + "."})

    start_name = g.nodes[start]["name"]
    end_node = g.nodes[end]
    lines = [f"Start at {_ref(start_name)}."]
    lines += [s["text"] for s in steps]
    if end_node["type"] != "exit":
        lines.append(f"You have arrived at {_ref(end_node['name'])}.")
 
    floors = sorted({g.nodes[n]["floor"] for n in route.nodes if g.nodes[n]["floor"] is not None})
    summary = (
        f"{_ref(start_name)[0].upper()}{_ref(start_name)[1:]} to {_ref(end_node['name'])}: "
        f"{_fmt_distance(route.distance, unit)} total, {len(steps)} step(s)"
        + (f", floors {floors[0]}–{floors[-1]}" if len(floors) > 1 else "")
        + "."
    )
 
    return {
        "start": start,
        "end": end,
        "total_distance": route.distance,
        "unit": unit,
        "summary": summary,
        "steps": steps,
        "text": "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1)),
    }