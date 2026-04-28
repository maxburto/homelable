import pytest
from unittest.mock import AsyncMock, patch
import json

from app.authority import READONLY_AUTHORITY, WRITE_AUTHORITY
from app.tools import (
    MUTATION_TOOL_NAMES,
    READONLY_TOOL_NAMES,
    _all_tools,
    _call_tool_for_authority,
    _dispatch,
    _filter_tools_for_authority,
)


@pytest.fixture
def mock_backend():
    with patch("app.tools.backend") as m:
        m.post = AsyncMock(return_value={"id": "1"})
        m.patch = AsyncMock(return_value={"id": "1"})
        m.delete = AsyncMock(return_value={})
        m.get = AsyncMock(return_value=[])
        yield m


@pytest.mark.anyio
async def test_create_node(mock_backend):
    result = await _dispatch("create_node", {"type": "server", "label": "Proxmox"})
    mock_backend.post.assert_called_once_with("/api/v1/nodes", {"type": "server", "label": "Proxmox"})
    assert result == {"id": "1"}


@pytest.mark.anyio
async def test_create_node_with_inventory_reference_fields(mock_backend):
    payload = {
        "type": "lxc",
        "label": "Homelable",
        "reference_document": "docs/reference/infrastructure/lxcs/lxc-151-homelable.md",
        "access_profiles": [{"host_id": "lxc-151-homelable", "transport": "api"}],
        "credential_refs": [{"credential_id": "api-homelable-mcp-api-key"}],
    }
    await _dispatch("create_node", payload.copy())
    mock_backend.post.assert_called_once_with("/api/v1/nodes", payload)


@pytest.mark.anyio
async def test_create_node_allows_group_rect_zone_fields(mock_backend):
    payload = {
        "type": "groupRect",
        "label": "PVE01 Zone",
        "pos_x": -80.0,
        "pos_y": 585.5,
        "custom_colors": {
            "border": "#22d3ee",
            "background": "#22d3ee14",
            "width": 5900.0,
            "height": 477.5,
            "z_order": 0,
        },
        "reference_document": "docs/reference/infrastructure/hardware/homelab-proxmox-node.md",
    }
    await _dispatch("create_node", payload.copy())
    mock_backend.post.assert_called_once_with("/api/v1/nodes", payload)


def test_create_node_schema_includes_group_rect_and_canvas_fields():
    create_tool = next(tool for tool in _all_tools() if tool.name == "create_node")
    properties = create_tool.inputSchema["properties"]
    assert "groupRect" in properties["type"]["enum"]
    assert "custom_colors" in properties
    assert "pos_x" in properties
    assert "pos_y" in properties


@pytest.mark.anyio
async def test_update_node(mock_backend):
    await _dispatch("update_node", {"id": "42", "label": "New name"})
    mock_backend.patch.assert_called_once_with("/api/v1/nodes/42", {"label": "New name"})


@pytest.mark.anyio
async def test_update_node_with_inventory_reference_fields(mock_backend):
    await _dispatch(
        "update_node",
        {
            "id": "42",
            "reference_document": "docs/reference/infrastructure/lxcs/lxc-151-homelable.md",
            "access_profiles": [{"host_id": "lxc-151-homelable", "transport": "api"}],
            "credential_refs": [{"credential_id": "api-homelable-mcp-api-key"}],
        },
    )
    mock_backend.patch.assert_called_once_with(
        "/api/v1/nodes/42",
        {
            "reference_document": "docs/reference/infrastructure/lxcs/lxc-151-homelable.md",
            "access_profiles": [{"host_id": "lxc-151-homelable", "transport": "api"}],
            "credential_refs": [{"credential_id": "api-homelable-mcp-api-key"}],
        },
    )


@pytest.mark.anyio
async def test_update_node_parent_id(mock_backend):
    await _dispatch("update_node", {"id": "42", "parent_id": "proxmox-1"})
    mock_backend.patch.assert_called_once_with("/api/v1/nodes/42", {"parent_id": "proxmox-1"})


@pytest.mark.anyio
async def test_delete_node(mock_backend):
    await _dispatch("delete_node", {"id": "42"})
    mock_backend.delete.assert_called_once_with("/api/v1/nodes/42")


@pytest.mark.anyio
async def test_create_edge(mock_backend):
    await _dispatch("create_edge", {"source": "1", "target": "2", "type": "ethernet"})
    mock_backend.post.assert_called_once_with("/api/v1/edges", {"source": "1", "target": "2", "type": "ethernet"})


@pytest.mark.anyio
async def test_delete_edge(mock_backend):
    await _dispatch("delete_edge", {"id": "99"})
    mock_backend.delete.assert_called_once_with("/api/v1/edges/99")


@pytest.mark.anyio
async def test_trigger_scan_no_ranges(mock_backend):
    await _dispatch("trigger_scan", {})
    mock_backend.post.assert_called_once_with("/api/v1/scan/trigger", {})


@pytest.mark.anyio
async def test_trigger_scan_with_ranges(mock_backend):
    await _dispatch("trigger_scan", {"ranges": ["192.168.1.0/24"]})
    mock_backend.post.assert_called_once_with("/api/v1/scan/trigger", {"ranges": ["192.168.1.0/24"]})


@pytest.mark.anyio
async def test_approve_device(mock_backend):
    mock_backend.get = AsyncMock(return_value=[
        {
            "id": "5",
            "ip": "192.168.1.50",
            "hostname": "server.local",
            "mac": "aa:bb:cc:dd:ee:ff",
            "os": "Linux",
            "suggested_type": "server",
            "services": [{"port": 22, "service_name": "SSH"}],
        }
    ])
    await _dispatch("approve_device", {"id": "5", "type": "server", "label": "MyServer"})
    mock_backend.get.assert_called_once_with("/api/v1/scan/pending")
    mock_backend.post.assert_called_once_with(
        "/api/v1/scan/pending/5/approve",
        {
            "type": "server",
            "label": "MyServer",
            "ip": "192.168.1.50",
            "hostname": "server.local",
            "mac": "aa:bb:cc:dd:ee:ff",
            "os": "Linux",
            "status": "unknown",
            "services": [{"port": 22, "service_name": "SSH"}],
        },
    )


@pytest.mark.anyio
async def test_approve_device_uses_pending_defaults(mock_backend):
    mock_backend.get = AsyncMock(return_value=[
        {
            "id": "5",
            "ip": "192.168.1.50",
            "hostname": None,
            "suggested_type": "lxc",
            "services": [],
        }
    ])
    await _dispatch("approve_device", {"id": "5"})
    mock_backend.post.assert_called_once_with(
        "/api/v1/scan/pending/5/approve",
        {
            "type": "lxc",
            "label": "192.168.1.50",
            "ip": "192.168.1.50",
            "status": "unknown",
        },
    )


@pytest.mark.anyio
async def test_hide_device(mock_backend):
    await _dispatch("hide_device", {"id": "5"})
    mock_backend.post.assert_called_once_with("/api/v1/scan/pending/5/hide", {})


@pytest.mark.anyio
async def test_get_canvas(mock_backend):
    mock_backend.get = AsyncMock(return_value={
        "nodes": [
            {
                "id": "n1",
                "type": "router",
                "position": {"x": 100, "y": 200},
                "width": 160,
                "height": 80,
                "data": {
                    "label": "Freebox",
                    "ip": "192.168.1.1",
                    "status": "online",
                    "reference_document": "docs/reference/infrastructure/network/router.md",
                    "access_profiles": [{"host_id": "router", "transport": "https"}],
                    "credential_refs": [{"credential_id": "app-router-admin-password"}],
                },
            }
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "type": "ethernet", "animated": True, "style": {"stroke": "#fff"}},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    })
    result = await _dispatch("get_canvas", {})
    mock_backend.get.assert_called_once_with("/api/v1/canvas")
    # Layout/style fields stripped, only semantic data kept
    assert result["nodes"] == [
        {
            "id": "n1",
            "node_type": "router",
            "label": "Freebox",
            "ip": "192.168.1.1",
            "status": "online",
            "reference_document": "docs/reference/infrastructure/network/router.md",
            "access_profiles": [{"host_id": "router", "transport": "https"}],
            "credential_refs": [{"credential_id": "app-router-admin-password"}],
        }
    ]
    assert result["edges"] == [{"id": "e1", "source": "n1", "target": "n2", "type": "ethernet"}]
    assert "viewport" not in result


@pytest.mark.anyio
async def test_get_canvas_slims_flat_backend_nodes(mock_backend):
    mock_backend.get = AsyncMock(return_value={
        "nodes": [
            {
                "id": "n1",
                "type": "lxc",
                "label": "Homelable",
                "ip": "192.168.20.151",
                "parent_id": "pve01",
                "pos_x": 100,
                "pos_y": 200,
                "reference_document": "docs/reference/infrastructure/lxcs/lxc-151-homelable.md",
                "access_profiles": [{"host_id": "lxc-151-homelable", "transport": "api"}],
                "credential_refs": [{"credential_id": "api-homelable-mcp-api-key"}],
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    })
    result = await _dispatch("get_canvas", {})
    assert result["nodes"] == [
        {
            "id": "n1",
            "type": "lxc",
            "node_type": "lxc",
            "label": "Homelable",
            "ip": "192.168.20.151",
            "parent_id": "pve01",
            "reference_document": "docs/reference/infrastructure/lxcs/lxc-151-homelable.md",
            "access_profiles": [{"host_id": "lxc-151-homelable", "transport": "api"}],
            "credential_refs": [{"credential_id": "api-homelable-mcp-api-key"}],
        }
    ]


@pytest.mark.anyio
async def test_list_nodes(mock_backend):
    mock_backend.get = AsyncMock(return_value=[{"id": "1", "label": "Freebox"}])
    result = await _dispatch("list_nodes", {})
    mock_backend.get.assert_called_once_with("/api/v1/nodes")
    assert result == [{"id": "1", "label": "Freebox"}]


@pytest.mark.anyio
async def test_list_pending_devices(mock_backend):
    mock_backend.get = AsyncMock(return_value=[{"id": "p1", "ip": "192.168.1.50"}])
    result = await _dispatch("list_pending_devices", {})
    mock_backend.get.assert_called_once_with("/api/v1/scan/pending")
    assert result == [{"id": "p1", "ip": "192.168.1.50"}]


@pytest.mark.anyio
async def test_unknown_tool():
    with pytest.raises(ValueError, match="Unknown tool"):
        await _dispatch("nonexistent", {})


def test_readonly_filter_tools_exposes_only_read_tools():
    names = {tool.name for tool in _filter_tools_for_authority(READONLY_AUTHORITY)}
    assert names == READONLY_TOOL_NAMES
    assert not names & MUTATION_TOOL_NAMES


def test_write_filter_tools_exposes_full_surface():
    names = {tool.name for tool in _filter_tools_for_authority(WRITE_AUTHORITY)}
    assert names == {tool.name for tool in _all_tools()}
    assert READONLY_TOOL_NAMES <= names
    assert MUTATION_TOOL_NAMES <= names


@pytest.mark.anyio
async def test_readonly_authority_blocks_mutation_tools(mock_backend):
    result = await _call_tool_for_authority(
        "create_node",
        {"type": "server", "label": "Blocked"},
        READONLY_AUTHORITY,
    )
    payload = json.loads(result[0].text)
    assert payload == {
        "blocked": True,
        "reason": "readonly_mcp_api_key",
        "tool": "create_node",
    }
    mock_backend.post.assert_not_called()


@pytest.mark.anyio
async def test_readonly_authority_allows_read_tools(mock_backend):
    mock_backend.get = AsyncMock(return_value=[{"id": "1", "label": "Freebox"}])
    result = await _call_tool_for_authority("list_nodes", {}, READONLY_AUTHORITY)
    payload = json.loads(result[0].text)
    mock_backend.get.assert_called_once_with("/api/v1/nodes")
    assert payload == [{"id": "1", "label": "Freebox"}]
