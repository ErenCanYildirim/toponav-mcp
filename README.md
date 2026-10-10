# TopoNav MCP

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant navigate a building. Rooms, corridors, stairs, elevators and exits are modeled as a graph, and the assistant can answer questions like:

- "How do I get from the Robotics Lab to the Main Entrance?"
- "What's the closest exit if I can't go through the kitchen?"
- "I use a wheelchair. How do I get from Office 101 to the Library?"
- "Which rooms can I reach from the lobby without using the West Corridor?"

Routes come back as step-by-step directions with distances, and when no route exists the server explains why: a locked door, stairs, an avoided location, or a one-way connection.

A Streamlit app shows the same routing on a floor plan.

## Example

> **User:** Using the building map, how do I get to the Server Room from the lobby?
>
> **Assistant:** You can get as far as the Upper Corridor, but the Server Room is behind a locked door and is marked as a restricted area.
>
> Lobby to Upper Corridor (11 m, step-free):
> 1. Walk to the elevator on floor 1 (3 m).
> 2. Take the elevator up to floor 2 (5 m).
> 3. Step out into the Upper Corridor (3 m).
>
> If you have authorization and a key or badge, tell me and I'll map that last stretch.

## Features

- **Shortest routes** by distance (Dijkstra), across multiple floors
- **Constraints:** avoid specific locations, step-free (accessible) routes only, allow or forbid locked doors
- **Nearest-of-type search:** closest exit, elevator, stairs, etc.
- **Reachability queries:** everywhere reachable from a point under given constraints
- **One-way connections**, e.g. emergency exits with no re-entry
- **Explained failures:** when no route exists, the server identifies which constraint blocks it
- **Map validation** with specific error messages (unknown nodes, duplicate ids, invalid distances, …)
- **Name or id lookup:** "Robotics Lab" and `lab_102` both work
- **Visual explorer:** a Streamlit app that draws the route on each floor plan

## Project structure

| File | Purpose |
|---|---|
| `graph.py` | Loads and validates the map, runs all graph queries (NetworkX) |
| `instructions.py` | Turns a route into human-readable directions |
| `server.py` | MCP server exposing the queries as tools (FastMCP) |
| `app.py` | Visual explorer (Streamlit + Plotly), uses the same core modules |
| `sample_building.json` | Two-floor example building used by default and by the tests |
| `tests/test_toponav.py` | Test suite (pytest) |

## Installation

Requires Python 3.10 or newer. Tested with Python 3.14 on Windows.

```bash
git clone https://github.com/<your-username>/toponav-mcp.git
cd toponav-mcp
python -m venv venv
```

Activate the environment:

- **Windows (PowerShell):** `venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source venv/bin/activate`

Then install the dependencies:

```bash
pip install -r requirements.txt
```

Check that the server starts:

```bash
python server.py
```

It should print the FastMCP banner and wait for a client. Stop it with Ctrl+C.

## Connecting to Claude Desktop

1. In Claude Desktop, open **Settings → Developer → Edit Config**.
2. Add the server under `mcpServers`, using **absolute paths**:

**Windows**
```json
{
  "mcpServers": {
    "toponav": {
      "command": "C:/path/to/toponav-mcp/venv/Scripts/python.exe",
      "args": ["C:/path/to/toponav-mcp/server.py"]
    }
  }
}
```

**macOS / Linux**
```json
{
  "mcpServers": {
    "toponav": {
      "command": "/path/to/toponav-mcp/venv/bin/python",
      "args": ["/path/to/toponav-mcp/server.py"]
    }
  }
}
```

3. Fully quit Claude Desktop (from the system tray or menu bar, not just the window) and reopen it.

### Troubleshooting

- **"Command not found" / `ENOENT`:** the Python path is wrong. On Windows the interpreter is in `Scripts\python.exe`, not `bin/python`, and the folder name must match your environment (`venv` vs `.venv`). With the environment activated, `python -c "import sys; print(sys.executable)"` prints the exact path to use.
- **"Connection closed":** the server crashed on start, usually because the dependencies were installed into a different Python. Run `python server.py` with that same interpreter to see the error.
- **"No map loaded":** the map file is missing or wasn't there when the server started. The map loads once at startup, so restart Claude Desktop after adding or moving it.
- **Logs:** `%APPDATA%\Claude\logs\` on Windows, `~/Library/Logs/Claude/` on macOS. Look for files starting with `mcp`.

## Tools

| Tool | Description |
|---|---|
| `list_locations` | List locations, optionally filtered by floor or type |
| `get_location` | Details for one location |
| `get_neighbors` | Directly connected locations, plus one-way connections leading in |
| `find_route` | Shortest route between two locations, with directions |
| `find_nearest` | Route to the closest location of a type (e.g. nearest exit) |
| `reachable_from` | All locations reachable from a starting point |
| `load_map` | Load a different map file |

The routing tools accept the same constraints:

| Parameter | Default | Effect |
|---|---|---|
| `avoid` | none | Locations the route must not pass through |
| `accessible_only` | `false` | Only step-free connections (no stairs) |
| `allow_locked` | `false` | Allow routes through locked doors |

Every route result states the conditions it was computed under.

## Visual explorer

```bash
streamlit run app.py
```

This opens the app in your browser (default `http://localhost:8501`). Pick a start and destination in the sidebar, or choose "Nearest exit", "Nearest elevator" or "Nearest stairs", and set the conditions: step-free only, allow locked doors, locations to avoid.

The plan shows:

- **The route** in green, with floor changes marked ("Stairs up to floor 2", "Arrive from floor 1")
- **Locked doors** as red dashed lines
- **Avoided locations** greyed out
- **One-way connections** with an arrow
- **Place types** by shape: square room, circle corridor, diamond exit, triangle stairs, hexagon elevator

When no route exists, the app shows the same explanation the MCP server returns.

The app imports `graph.py` and `instructions.py` directly; it doesn't go through MCP. The core logic is independent of the interface, and both the assistant and the app use it.

Floor plans are drawn from the optional `position` field on each node (see the map format below). Nodes without a position are placed automatically, which works but looks less like a building.

## Using your own map

By default the server loads `sample_building.json` from its own folder. To use another map, set the `TOPONAV_MAP` environment variable in the Claude Desktop config:

```json
"env": { "TOPONAV_MAP": "C:/path/to/my_building.json" }
```

You can also ask the assistant to load a map by path during a conversation (`load_map` tool).

### Map format

A map has three sections: `meta`, `nodes` and `edges`.

**`meta`**: building name, distance unit, and defaults that apply to every edge unless overridden.

```json
"meta": {
  "name": "My Building",
  "distance_unit": "meters",
  "defaults": { "bidirectional": true, "accessible": true, "locked": false }
}
```

**`nodes`**: the places.

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique identifier, e.g. `lab_102` |
| `name` | no | Display name used in directions (defaults to the id) |
| `type` | yes | `room`, `corridor`, `exit`, `stairs` or `elevator` |
| `floor` | no | Floor number, used to describe floor changes |
| `tags` | no | e.g. `["restricted"]`, which is mentioned when a route is blocked |
| `position` | no | `{ "x": 10, "y": 4 }`, used only by the visual explorer to draw the plan. Give stairs and elevators the same position on every floor so they line up |

**`edges`**: the connections.

| Field | Required | Description |
|---|---|---|
| `source`, `target` | yes | Node ids |
| `distance` | yes | Positive number, in the unit from `meta` |
| `bidirectional` | no | `false` makes the connection one-way (source → target) |
| `accessible` | no | `false` for stairs or other non-step-free connections |
| `locked` | no | `true` for doors that need a key or badge |
| `description` | no | Added to the directions, e.g. `"through the side door"` |

To connect floors, add an edge between the stairs or elevator nodes on each floor (for example `elevator_1` → `elevator_2`). The directions will then say "Take the elevator up to floor 2".

## Running the tests

```bash
pip install pytest
pytest
```

The tests cover map validation, all routing scenarios (constraints, locked doors, one-way exits, failure explanations), the generated directions, and the MCP server layer through a real MCP client. They rely on the exact contents of `sample_building.json`, so use a separate file if you want different test data.

## Known limitations

- **No turn-by-turn directions.** Routing is topological: it uses which places connect, not where they are, so it can't say "turn left". Directions are a sequence of places with distances. Node positions exist but are currently only used for drawing.
- **Distances only.** There are no walking-time estimates.
- **Static data.** The map has no live status, such as an elevator being out of service or a door being temporarily locked.
- **Exact name matching.** Locations must be named exactly (case-insensitive) or by id. The assistant usually handles this by listing locations first, but there is no fuzzy matching in the server.
- **One map at a time.** Loading a map replaces the current one.
- **Ambiguous questions in general-purpose assistants.** In Claude Desktop, TopoNav is one tool among many. A question like "How do I get into the server room?" with no mention of a building may be answered as a general access-control question rather than with the map. Mentioning the building or map avoids this. A dedicated app would set this context in its own system prompt.

## Roadmap

- Turn-by-turn directions ("turn left"), using the node positions
- Fuzzy name matching
- Walking-time estimates
- Multiple loaded maps
- Hosted demo of the visual explorer