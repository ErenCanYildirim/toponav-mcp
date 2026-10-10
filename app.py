"""TopoNav visual explorer.

Run:  streamlit run app.py
Uses graph.py and instructions.py directly (no MCP involved).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import networkx as nx
import plotly.graph_objects as go
import streamlit as st

from graph import MapError, NodeNotFoundError, NoRouteError, TopoMap
from instructions import describe_route

ROOT = Path(__file__).parent
MAP_PATH = Path(os.environ.get("TOPONAV_MAP", ROOT / "sample_building.json"))

# Wayfinding palette: muted plan, one strong route color (exit-sign green).
GROUND = "#F2F4F3"
INK = "#1E2B2F"
MUTED = "#7B8A8C"
LINE = "#AEB8B6"
ROUTE = "#00855A"
LOCKED = "#C62D2D"
AVOIDED = "#D5DAD9"
FONT = "Atkinson Hyperlegible, Source Sans Pro, sans-serif"

SYMBOLS = {"room": "square", "corridor": "circle", "exit": "diamond",
           "stairs": "triangle-up", "elevator": "hexagon"}
SIZES = {"room": 16, "corridor": 10, "exit": 16, "stairs": 15, "elevator": 15}

NEAREST = {"__exit": "Nearest exit", "__elevator": "Nearest elevator", "__stairs": "Nearest stairs"}


# ---------- data ----------

@st.cache_resource
def load_map(path: str) -> tuple[TopoMap, dict[str, tuple[float, float]]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    m = TopoMap(raw)
    positions = {n["id"]: (n["position"]["x"], n["position"]["y"])
                 for n in raw["nodes"] if "position" in n}

    # Nodes without a position get an automatic layout, per floor.
    missing = [n for n in m.graph.nodes if n not in positions]
    for floor in {m.graph.nodes[n]["floor"] for n in missing}:
        sub = m.graph.subgraph(n for n in m.graph.nodes if m.graph.nodes[n]["floor"] == floor)
        layout = nx.spring_layout(sub.to_undirected(), seed=1, scale=10)
        for n in sub.nodes:
            positions.setdefault(n, tuple(layout[n]))
    return m, positions


# ---------- drawing ----------

def floor_figure(m: TopoMap, pos, floor, route_nodes, avoid_ids, start, end) -> go.Figure:
    g = m.graph
    on_floor = [n for n in g.nodes if g.nodes[n]["floor"] == floor]
    route_edges = set(zip(route_nodes, route_nodes[1:]))
    fig = go.Figure()

    # Connections: one line per pair, styled by type.
    drawn = set()
    for u, v, e in g.edges(data=True):
        if g.nodes[u]["floor"] != floor or g.nodes[v]["floor"] != floor:
            continue
        pair = frozenset((u, v))
        if pair in drawn:
            continue
        drawn.add(pair)
        on_route = (u, v) in route_edges or (v, u) in route_edges
        color, dash, width = LINE, "solid", 2
        if e["locked"]:
            color, dash = LOCKED, "dash"
        elif not e["accessible"]:
            dash = "dot"
        if on_route:
            color, width = ROUTE, 6
        (x0, y0), (x1, y1) = pos[u], pos[v]
        fig.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode="lines", hoverinfo="skip",
                                 line=dict(color=color, width=width, dash=dash), showlegend=False))
        if not g.has_edge(v, u):  # one-way: arrow pointing to the target
            fig.add_annotation(x=x1, y=y1, ax=(x0 + x1) / 2, ay=(y0 + y1) / 2,
                               xref="x", yref="y", axref="x", ayref="y", showarrow=True,
                               arrowhead=2, arrowsize=1.4, arrowcolor=color, text="")

    # Places
    for n in on_floor:
        d = g.nodes[n]
        x, y = pos[n]
        if n in avoid_ids:
            fill, edge = AVOIDED, MUTED
        elif n in route_nodes:
            fill, edge = ROUTE, ROUTE
        else:
            fill, edge = "white", INK
        is_end = n in (start, end)
        # Put the label on the side away from connected places, to keep lines clear.
        nbr_y = [pos[k][1] for k in set(g.successors(n)) | set(g.predecessors(n))
                 if g.nodes[k]["floor"] == floor]
        below = bool(nbr_y) and sum(nbr_y) / len(nbr_y) > y
        size = SIZES.get(d["type"], 14) + (6 if is_end else 0)
        short = re.sub(r"\s*\(floor \d+\)$", "", d["name"], flags=re.I)  # floor is implied by the plan
        label = f"<b>{short}</b>" if is_end else short
        hover = f"{d['name']}<br>{d['type']}, floor {d['floor']}"
        if d.get("tags"):
            hover += f"<br>{', '.join(d['tags'])}"
        fig.add_trace(go.Scatter(
            x=[x], y=[y], mode="markers+text", text=[label],
            textposition="bottom center" if below else "top center",
            textfont=dict(color=MUTED if n in avoid_ids else INK, size=12),
            marker=dict(symbol=SYMBOLS.get(d["type"], "circle"), size=size, color=fill,
                        line=dict(color=edge, width=2)),
            hovertext=[hover], hoverinfo="text", showlegend=False))

    # Floor changes along the route
    for u, v in zip(route_nodes, route_nodes[1:]):
        fu, fv = g.nodes[u]["floor"], g.nodes[v]["floor"]
        if fu == fv:
            continue
        mode = "elevator" if g.nodes[u]["type"] == "elevator" else "stairs"
        if fu == floor:
            arrow = "up" if fv > fu else "down"
            note, anchor = f"{mode.capitalize()} {arrow} to floor {fv}", u
        elif fv == floor:
            note, anchor = f"Arrive from floor {fu}", v
        else:
            continue
        x, y = pos[anchor]
        fig.add_annotation(x=x, y=y, text=note, showarrow=False, yshift=-26,
                           font=dict(color=ROUTE, size=12), bgcolor="white",
                           bordercolor=ROUTE, borderwidth=1, borderpad=3)

    # Same scale on every floor, so stairs and elevators line up between plans.
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    fig.update_layout(
        title=dict(text=f"Floor {floor}", font=dict(size=18, color=INK), x=0.02),
        plot_bgcolor=GROUND, paper_bgcolor=GROUND, font=dict(family=FONT),
        margin=dict(l=10, r=10, t=44, b=10), height=360, dragmode="pan",
        xaxis=dict(visible=False, range=[min(xs) - 4, max(xs) + 4]),
        yaxis=dict(visible=False, range=[min(ys) - 2.5, max(ys) + 2.5],
                   scaleanchor="x", scaleratio=1),
    )
    return fig


# ---------- page ----------

st.set_page_config(page_title="TopoNav", page_icon="🧭", layout="wide")
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
html, body, [class*="st-"], h1, h2, h3, p, li, label {{ font-family: {FONT}; }}
h1 {{ font-weight: 700; letter-spacing: -0.01em; }}
.legend {{ color: {MUTED}; font-size: 0.9rem; line-height: 1.8; }}
.swatch {{ display: inline-block; width: 22px; height: 0; vertical-align: middle;
           margin: 0 6px 0 14px; }}
.swatch:first-child {{ margin-left: 0; }}
.result {{ font-size: 1.15rem; color: {INK}; margin: 0.25rem 0 1rem; }}
</style>
""", unsafe_allow_html=True)

try:
    topo, positions = load_map(str(MAP_PATH))
except (MapError, FileNotFoundError, json.JSONDecodeError) as e:
    st.error(f"Could not load the map at {MAP_PATH}: {e}")
    st.stop()

g = topo.graph
ids = sorted(g.nodes, key=lambda n: (g.nodes[n]["floor"] or 0, g.nodes[n]["name"]))
names = {n: f"{g.nodes[n]['name']} (floor {g.nodes[n]['floor']})" for n in ids}

with st.sidebar:
    st.header("Plan a route")
    start = st.selectbox("From", ids, index=ids.index("lab_102") if "lab_102" in ids else 0,
                         format_func=names.get)
    dest_options = list(NEAREST) + [n for n in ids if n != start]
    default_dest = "entrance_main" if "entrance_main" in dest_options else dest_options[0]
    dest = st.selectbox("To", dest_options, index=dest_options.index(default_dest),
                        format_func=lambda d: NEAREST.get(d) or names[d])
    st.subheader("Conditions")
    accessible = st.toggle("Step-free only", help="Never use stairs.")
    allow_locked = st.toggle("Allow locked doors", help="Only if you have a key or badge.")
    avoid = st.multiselect("Avoid", [n for n in ids if n != start and n != dest],
                           format_func=names.get)

route, result, error = None, None, None
try:
    if dest in NEAREST:
        route = topo.nearest_of_type(start, dest.removeprefix("__"), avoid, accessible, allow_locked)
    else:
        route = topo.shortest_route(start, dest, avoid, accessible, allow_locked)
    result = describe_route(topo, route)
except (NoRouteError, NodeNotFoundError) as e:
    error = str(e)

st.title("TopoNav")
st.caption(topo.meta.get("name", ""))

plans, side = st.columns([5, 2], gap="large")

route_nodes = route.nodes if route else []
end_node = route_nodes[-1] if route_nodes else (None if dest in NEAREST else dest)
with plans:
    for floor in sorted({g.nodes[n]["floor"] for n in g.nodes}, reverse=True):
        st.plotly_chart(
            floor_figure(topo, positions, floor, route_nodes, set(avoid), start, end_node),
            width="stretch", config={"displayModeBar": False, "scrollZoom": True})
    st.markdown(f"""<div class="legend">
<span class="swatch" style="border-top: 5px solid {ROUTE}"></span>Your route
<span class="swatch" style="border-top: 2px dashed {LOCKED}"></span>Locked door
<span class="swatch" style="border-top: 4px solid {AVOIDED}"></span>Avoided<br>
Square room, circle corridor, diamond exit, triangle stairs, hexagon elevator
</div>""", unsafe_allow_html=True)

with side:
    if result:
        st.markdown(f'<p class="result">{result["summary"]}</p>', unsafe_allow_html=True)
        st.subheader("Directions")
        steps = [line.split(". ", 1)[1] for line in result["text"].splitlines()]
        # Keep distances attached to their step when the column wraps.
        steps = [t.replace(" — ", "\u00a0—\u00a0").replace(" m.", "\u00a0m.") for t in steps]
        st.markdown("\n".join(f"1. {t}" for t in steps))
    else:
        st.error(error)
        st.caption("Change the conditions in the sidebar to find a route.")