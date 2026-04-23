import pytest
from unittest.mock import AsyncMock, patch

from app.auth import resolve_authority_for_api_key
from app.authority import READONLY_AUTHORITY, WRITE_AUTHORITY
from app.config import settings


@pytest.mark.anyio
async def test_health_no_key(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_missing_api_key(client):
    resp = await client.get("/mcp")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_wrong_api_key(client):
    resp = await client.get("/mcp", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_valid_api_key_passes(client, api_key):
    # Auth passes — mock handle_request so we don't need a live MCP session
    with patch("app.main.session_manager.handle_request", new_callable=AsyncMock):
        resp = await client.get("/mcp", headers={"X-API-Key": api_key})
    assert resp.status_code != 401


@pytest.mark.anyio
async def test_write_api_key_passes(client, write_api_key):
    with patch("app.main.session_manager.handle_request", new_callable=AsyncMock):
        resp = await client.get("/mcp", headers={"X-API-Key": write_api_key})
    assert resp.status_code != 401


def test_resolve_authority_for_split_keys(api_key, write_api_key, monkeypatch):
    monkeypatch.setattr(settings, "mcp_api_key", api_key)
    monkeypatch.setattr(settings, "mcp_write_api_key", write_api_key)

    assert resolve_authority_for_api_key(api_key) == READONLY_AUTHORITY
    assert resolve_authority_for_api_key(write_api_key) == WRITE_AUTHORITY
    assert resolve_authority_for_api_key("wrong") is None


def test_resolve_authority_legacy_single_key(api_key, monkeypatch):
    monkeypatch.setattr(settings, "mcp_api_key", api_key)
    monkeypatch.setattr(settings, "mcp_write_api_key", "")

    assert resolve_authority_for_api_key(api_key) == WRITE_AUTHORITY
    assert resolve_authority_for_api_key("wrong") is None
