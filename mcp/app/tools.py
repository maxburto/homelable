import json
from mcp.server import Server
from mcp.types import Tool, TextContent
from .backend_client import backend


def register_tools(server: Server):

    @server.list_tools()
    async def list_tools():
        return [
            Tool(name="create_node", description="Add a new node to the homelab canvas", inputSchema={
                "type": "object",
                "required": ["type", "label"],
                "properties": {
                    "type":     {"type": "string", "enum": ["isp","router","switch","server","proxmox","vm","lxc","nas","iot","ap","generic"]},
                    "label":    {"type": "string"},
                    "ip":       {"type": "string"},
                    "hostname": {"type": "string"},
                    "status":   {"type": "string", "enum": ["online","offline","unknown","pending"], "default": "unknown"},
                    "reference_document": {"type": "string", "description": "Repo-relative path to the node's human-readable reference document."},
                    "access_profiles": {"type": "array", "items": {"type": "object"}, "description": "Non-secret access metadata for this node."},
                    "credential_refs": {"type": "array", "items": {"type": "object"}, "description": "Non-secret references to Credential Ops credential IDs and approved flows."},
                },
            }),
            Tool(name="update_node", description="Update an existing node", inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id":        {"type": "string"},
                    "label":     {"type": "string"},
                    "ip":        {"type": "string"},
                    "hostname":  {"type": "string"},
                    "status":    {"type": "string"},
                    "parent_id": {"type": "string", "description": "ID of the parent node (e.g. Proxmox host for a VM/LXC). Pass null to detach."},
                    "reference_document": {"type": "string", "description": "Repo-relative path to the node's human-readable reference document."},
                    "access_profiles": {"type": "array", "items": {"type": "object"}, "description": "Non-secret access metadata for this node."},
                    "credential_refs": {"type": "array", "items": {"type": "object"}, "description": "Non-secret references to Credential Ops credential IDs and approved flows."},
                },
            }),
            Tool(name="delete_node", description="Delete a node from the canvas", inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            }),
            Tool(name="create_edge", description="Create a network link between two nodes", inputSchema={
                "type": "object",
                "required": ["source", "target"],
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "type":   {"type": "string", "enum": ["ethernet","wifi","iot","vlan","virtual"], "default": "ethernet"},
                    "label":  {"type": "string"},
                },
            }),
            Tool(name="delete_edge", description="Delete a network link", inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            }),
            Tool(name="trigger_scan", description="Trigger a network discovery scan", inputSchema={
                "type": "object",
                "properties": {
                    "ranges": {"type": "array", "items": {"type": "string"}, "description": "CIDR ranges to scan (uses configured defaults if omitted)"},
                },
            }),
            Tool(name="approve_device", description="Approve a pending discovered device and create a node", inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id":    {"type": "string"},
                    "type":  {"type": "string", "enum": ["isp","router","switch","server","proxmox","vm","lxc","nas","iot","ap","generic"], "default": "generic"},
                    "label": {"type": "string"},
                },
            }),
            Tool(name="hide_device", description="Hide a pending discovered device", inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            }),
            Tool(name="get_canvas", description="Get the full canvas: all nodes and edges in the homelab topology", inputSchema={
                "type": "object",
                "properties": {},
            }),
            Tool(name="list_nodes", description="List all nodes (devices) in the homelab", inputSchema={
                "type": "object",
                "properties": {},
            }),
            Tool(name="list_pending_devices", description="List devices discovered by scan but not yet approved or hidden", inputSchema={
                "type": "object",
                "properties": {},
            }),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]


def _slim_canvas(raw: dict) -> dict:
    """Strip React Flow layout/style fields — keep only semantic data for AI use."""
    NODE_KEEP = {
        "id",
        "type",
        "label",
        "ip",
        "hostname",
        "status",
        "services",
        "description",
        "parent_id",
        "parentId",
        "reference_document",
        "access_profiles",
        "credential_refs",
    }
    EDGE_KEEP = {"id", "source", "target", "type", "label"}

    def slim_node(n: dict) -> dict:
        raw_data = n.get("data")
        data = raw_data if isinstance(raw_data, dict) else n
        out = {k: v for k, v in data.items() if k in NODE_KEEP and v not in (None, "", [])}
        out["id"] = n.get("id") or data.get("id")
        out["node_type"] = n.get("type") or data.get("type")
        return out

    def slim_edge(e: dict) -> dict:
        return {k: v for k, v in e.items() if k in EDGE_KEEP and v not in (None, "")}

    return {
        "nodes": [slim_node(n) for n in raw.get("nodes", [])],
        "edges": [slim_edge(e) for e in raw.get("edges", [])],
    }


async def _dispatch(name: str, args: dict) -> dict:
    if name == "create_node":
        return await backend.post("/api/v1/nodes", args)

    if name == "update_node":
        node_id = args.pop("id")
        return await backend.patch(f"/api/v1/nodes/{node_id}", args)

    if name == "delete_node":
        return await backend.delete(f"/api/v1/nodes/{args['id']}")

    if name == "create_edge":
        return await backend.post("/api/v1/edges", args)

    if name == "delete_edge":
        return await backend.delete(f"/api/v1/edges/{args['id']}")

    if name == "trigger_scan":
        body = {"ranges": args["ranges"]} if "ranges" in args else {}
        return await backend.post("/api/v1/scan/trigger", body)

    if name == "approve_device":
        device_id = args.pop("id")
        return await backend.post(f"/api/v1/scan/pending/{device_id}/approve", args)

    if name == "hide_device":
        return await backend.post(f"/api/v1/scan/pending/{args['id']}/hide", {})

    if name == "get_canvas":
        raw = await backend.get("/api/v1/canvas")
        return _slim_canvas(raw)

    if name == "list_nodes":
        return await backend.get("/api/v1/nodes")

    if name == "list_pending_devices":
        return await backend.get("/api/v1/scan/pending")

    raise ValueError(f"Unknown tool: {name}")
