import json
from mcp.server import Server
from mcp.types import Tool, TextContent
from .authority import READONLY_AUTHORITY, WRITE_AUTHORITY, get_current_authority
from .backend_client import backend


NODE_TYPES = [
    "isp",
    "router",
    "switch",
    "server",
    "proxmox",
    "vm",
    "lxc",
    "nas",
    "iot",
    "ap",
    "service",
    "generic",
    "groupRect",
]
READONLY_TOOL_NAMES = frozenset({"get_canvas", "list_nodes", "list_pending_devices"})
MUTATION_TOOL_NAMES = frozenset(
    {
        "approve_device",
        "create_edge",
        "create_node",
        "delete_edge",
        "delete_node",
        "hide_device",
        "trigger_scan",
        "update_node",
    }
)
NODE_METADATA_SCHEMA = {
    "type": {"type": "string", "enum": NODE_TYPES},
    "label": {"type": "string"},
    "ip": {"type": "string"},
    "hostname": {"type": "string"},
    "mac": {"type": "string"},
    "os": {"type": "string"},
    "status": {"type": "string", "enum": ["online", "offline", "unknown", "pending"], "default": "unknown"},
    "check_method": {"type": "string"},
    "check_target": {"type": "string"},
    "services": {"type": "array", "items": {"type": "object"}},
    "notes": {"type": "string"},
    "parent_id": {"type": ["string", "null"], "description": "ID of the parent node (e.g. Proxmox host for a VM/LXC). Pass null to detach."},
    "container_mode": {"type": "boolean"},
    "reference_document": {"type": "string", "description": "Repo-relative path to the node's human-readable reference document."},
    "access_profiles": {"type": "array", "items": {"type": "object"}, "description": "Non-secret access metadata for this node."},
    "credential_refs": {"type": "array", "items": {"type": "object"}, "description": "Non-secret references to Credential Ops credential IDs and approved flows."},
    "properties": {"type": "array", "items": {"type": "object"}},
    "pos_x": {"type": "number"},
    "pos_y": {"type": "number"},
    "custom_colors": {"type": ["object", "null"], "description": "Canvas styling and dimensions for visual-only nodes such as zones."},
    "custom_icon": {"type": ["string", "null"]},
    "width": {"type": ["number", "null"]},
    "height": {"type": ["number", "null"]},
    "bottom_handles": {"type": "integer"},
}


def _all_tools() -> list[Tool]:
    return [
        Tool(name="create_node", description="Add a new node to the homelab canvas", inputSchema={
            "type": "object",
            "required": ["type", "label"],
            "properties": NODE_METADATA_SCHEMA,
        }),
        Tool(name="update_node", description="Update an existing node", inputSchema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}, **NODE_METADATA_SCHEMA},
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
                "type":  {"type": "string", "enum": NODE_TYPES, "default": "generic"},
                "label": {"type": "string"},
                "reference_document": NODE_METADATA_SCHEMA["reference_document"],
                "access_profiles": NODE_METADATA_SCHEMA["access_profiles"],
                "credential_refs": NODE_METADATA_SCHEMA["credential_refs"],
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


def _filter_tools_for_authority(authority: str) -> list[Tool]:
    tools = _all_tools()
    if authority == WRITE_AUTHORITY:
        return tools
    return [tool for tool in tools if tool.name in READONLY_TOOL_NAMES]


def _is_mutation_tool(name: str) -> bool:
    return name in MUTATION_TOOL_NAMES


async def _call_tool_for_authority(name: str, arguments: dict, authority: str) -> list[TextContent]:
    if authority == READONLY_AUTHORITY and _is_mutation_tool(name):
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "blocked": True,
                        "reason": "readonly_mcp_api_key",
                        "tool": name,
                    }
                ),
            )
        ]

    result = await _dispatch(name, arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


def register_tools(server: Server):

    @server.list_tools()
    async def list_tools():
        return _filter_tools_for_authority(get_current_authority())

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        return await _call_tool_for_authority(name, arguments, get_current_authority())


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
        "properties",
        "notes",
        "check_method",
        "check_target",
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
        payload = await _approve_device_payload(args)
        device_id = payload.pop("id")
        return await backend.post(f"/api/v1/scan/pending/{device_id}/approve", payload)

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


async def _approve_device_payload(args: dict) -> dict:
    device_id = args["id"]
    pending = await backend.get("/api/v1/scan/pending")
    device = next(
        (item for item in pending if isinstance(item, dict) and item.get("id") == device_id),
        {},
    )
    payload = {
        "id": device_id,
        "type": args.get("type") or device.get("suggested_type") or "generic",
        "label": args.get("label") or device.get("hostname") or device.get("ip") or "Discovered device",
        "ip": device.get("ip"),
        "hostname": device.get("hostname"),
        "mac": device.get("mac"),
        "os": device.get("os"),
        "status": args.get("status") or "unknown",
        "services": device.get("services") or [],
    }
    for key in ("reference_document", "access_profiles", "credential_refs"):
        if key in args:
            payload[key] = args[key]
    return {key: value for key, value in payload.items() if value not in (None, "", [])}
