"""Tests for status_checker service: each check method."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services.status_checker import _ping, _tcp_connect, check_node

# --- check_node dispatcher ---

@pytest.mark.asyncio
async def test_check_node_none_always_online():
    result = await check_node("none", None, None)
    assert result["status"] == "online"
    assert result["response_time_ms"] is None


@pytest.mark.asyncio
async def test_check_node_none_always_online_ignores_host():
    """'none' returns online immediately without any network call."""
    with patch("app.services.status_checker._ping", new_callable=AsyncMock) as mock_ping:
        result = await check_node("none", None, "192.168.1.1")
    mock_ping.assert_not_called()
    assert result["status"] == "online"


@pytest.mark.asyncio
async def test_check_node_unknown_without_host():
    result = await check_node("ping", None, None)
    assert result["status"] == "unknown"
    assert result["response_time_ms"] is None


@pytest.mark.asyncio
async def test_check_node_ping_online():
    with patch("app.services.status_checker._ping", new_callable=AsyncMock, return_value=True):
        result = await check_node("ping", None, "192.168.1.1")
    assert result["status"] == "online"
    assert result["response_time_ms"] is not None


@pytest.mark.asyncio
async def test_check_node_ping_offline():
    with patch("app.services.status_checker._ping", new_callable=AsyncMock, return_value=False):
        result = await check_node("ping", None, "192.168.1.1")
    assert result["status"] == "offline"


@pytest.mark.asyncio
async def test_check_node_http_online():
    with patch("app.services.status_checker._http_get", new_callable=AsyncMock, return_value=True):
        result = await check_node("http", "192.168.1.1:8080", None)
    assert result["status"] == "online"


@pytest.mark.asyncio
async def test_check_node_http_prepends_scheme():
    """If target doesn't start with http, http:// is prepended."""
    captured = {}

    async def fake_http_get(url, verify=False):
        captured["url"] = url
        return True

    with patch("app.services.status_checker._http_get", side_effect=fake_http_get):
        await check_node("http", "192.168.1.1:8080", None)

    assert captured["url"].startswith("http://")


@pytest.mark.asyncio
async def test_check_node_https_uses_verify():
    captured = {}

    async def fake_http_get(url, verify=False):
        captured["verify"] = verify
        return True

    with patch("app.services.status_checker._http_get", side_effect=fake_http_get):
        await check_node("https", "https://myserver", None)

    assert captured["verify"] is True


@pytest.mark.asyncio
async def test_check_node_ssh():
    with patch("app.services.status_checker._tcp_connect", new_callable=AsyncMock, return_value=True) as mock_tcp:
        result = await check_node("ssh", None, "192.168.1.5")
    mock_tcp.assert_called_once_with("192.168.1.5", 22)
    assert result["status"] == "online"


@pytest.mark.asyncio
async def test_check_node_tcp_parses_port():
    captured = {}

    async def fake_tcp(host, port):
        captured["host"] = host
        captured["port"] = port
        return True

    with patch("app.services.status_checker._tcp_connect", side_effect=fake_tcp):
        await check_node("tcp", "192.168.1.10:9090", None)

    assert captured["host"] == "192.168.1.10"
    assert captured["port"] == 9090


@pytest.mark.asyncio
async def test_check_node_prometheus_appends_metrics():
    captured = {}

    async def fake_http_get(url, verify=False):
        captured["url"] = url
        return True

    with patch("app.services.status_checker._http_get", side_effect=fake_http_get):
        await check_node("prometheus", "192.168.1.10:9090", None)

    assert "/metrics" in captured["url"]


@pytest.mark.asyncio
async def test_check_node_health_appends_health():
    captured = {}

    async def fake_http_get(url, verify=False):
        captured["url"] = url
        return True

    with patch("app.services.status_checker._http_get", side_effect=fake_http_get):
        await check_node("health", "192.168.1.10:8080", None)

    assert "/health" in captured["url"]


@pytest.mark.asyncio
async def test_check_node_unknown_method_falls_back_to_ping():
    with patch("app.services.status_checker._ping", new_callable=AsyncMock, return_value=True) as mock_ping:
        result = await check_node("foobar", None, "10.0.0.1")
    mock_ping.assert_called_once()
    assert result["status"] == "online"


@pytest.mark.asyncio
async def test_check_node_exception_returns_offline():
    with patch("app.services.status_checker._ping", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        result = await check_node("ping", None, "10.0.0.1")
    assert result["status"] == "offline"
    assert result["response_time_ms"] is None


@pytest.mark.asyncio
async def test_check_node_proxmox_vm_uses_runtime_status_and_guest_agent(monkeypatch):
    monkeypatch.setattr(settings, "proxmox_api_profiles", {
        "homelab": {
            "base_url": "https://pve.example:8006",
            "node": "homelab",
            "token_id": "token-id",
            "token_secret": "token-secret",
            "verify_ssl": False,
        },
    })

    async def fake_proxmox_get(profile, path):
        if path == "nodes/homelab/qemu/112/status/current":
            return {"data": {"status": "running", "name": "database-server-dev"}}
        if path == "nodes/homelab/qemu/112/agent/ping":
            return {"data": {"result": {}}}
        if path == "nodes/homelab/qemu/112/agent/network-get-interfaces":
            return {"data": {"result": [
                {"name": "docker0", "ip-addresses": [
                    {"ip-address-type": "ipv4", "ip-address": "172.17.0.1"},
                ]},
                {"name": "enp6s18", "ip-addresses": [
                    {"ip-address-type": "ipv4", "ip-address": "192.168.20.87"},
                ]},
            ]}}
        if path == "nodes/homelab/qemu/112/agent/get-host-name":
            return {"data": {"result": {"host-name": "db01"}}}
        if path == "nodes/homelab/qemu/112/agent/get-osinfo":
            return {"data": {"result": {"pretty-name": "Ubuntu 24.04 LTS"}}}
        raise AssertionError(f"unexpected path {path}")

    with patch("app.services.status_checker._proxmox_api_get", side_effect=fake_proxmox_get):
        result = await check_node("proxmox-vm", "proxmox://homelab/qemu/112", "192.168.20.87")

    assert result["status"] == "online"
    assert result["ip"] == "192.168.20.87"
    assert result["hostname"] == "db01"
    props = {prop["key"]: prop["value"] for prop in result["properties"]}
    assert props["Runtime State"] == "running"
    assert props["Guest Agent"] == "online"
    assert props["Guest OS"] == "Ubuntu 24.04 LTS"


@pytest.mark.asyncio
async def test_check_node_proxmox_lxc_uses_runtime_status(monkeypatch):
    monkeypatch.setattr(settings, "proxmox_api_profiles", {
        "homelab": {
            "base_url": "https://pve.example:8006",
            "node": "homelab",
            "token_id": "token-id",
            "token_secret": "token-secret",
        },
    })

    async def fake_proxmox_get(profile, path):
        assert path == "nodes/homelab/lxc/119/status/current"
        return {"data": {"status": "running", "name": "prometheus-pve-exporter"}}

    with patch("app.services.status_checker._proxmox_api_get", side_effect=fake_proxmox_get):
        result = await check_node("proxmox-lxc", "proxmox://homelab/lxc/119", None)

    assert result["status"] == "online"
    props = {prop["key"]: prop["value"] for prop in result["properties"]}
    assert props["CTID"] == "119"
    assert props["Runtime State"] == "running"


@pytest.mark.asyncio
async def test_check_node_proxmox_stopped_guest_is_offline(monkeypatch):
    monkeypatch.setattr(settings, "proxmox_api_profiles", {
        "homelab": {
            "base_url": "https://pve.example:8006",
            "node": "homelab",
            "token_id": "token-id",
            "token_secret": "token-secret",
        },
    })

    async def fake_proxmox_get(profile, path):
        assert path == "nodes/homelab/qemu/114/status/current"
        return {"data": {"status": "stopped", "name": "ai-ubuntu-server"}}

    with patch("app.services.status_checker._proxmox_api_get", side_effect=fake_proxmox_get):
        result = await check_node("proxmox-vm", "proxmox://homelab/qemu/114", None)

    assert result["status"] == "offline"


@pytest.mark.asyncio
async def test_check_node_proxmox_missing_profile_is_unknown(monkeypatch):
    monkeypatch.setattr(settings, "proxmox_api_profiles", {})
    result = await check_node("proxmox-vm", "proxmox://missing/qemu/112", None)
    assert result["status"] == "unknown"
    assert result["response_time_ms"] is None


# --- _ping platform args ---

@pytest.mark.asyncio
async def test_ping_uses_unix_args_on_non_windows():
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        proc = MagicMock()
        proc.returncode = 0
        proc.wait = AsyncMock()
        return proc

    with patch("app.services.status_checker.sys.platform", "linux"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await _ping("192.168.1.1")

    assert "-c" in captured["args"]
    assert "-W" in captured["args"]
    assert "-n" not in captured["args"]


@pytest.mark.asyncio
async def test_ping_uses_windows_args_on_win32():
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        proc = MagicMock()
        proc.returncode = 0
        proc.wait = AsyncMock()
        return proc

    with patch("app.services.status_checker.sys.platform", "win32"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await _ping("192.168.1.1")

    assert "-n" in captured["args"]
    assert "-w" in captured["args"]
    assert "-c" not in captured["args"]


# --- _tcp_connect ---

@pytest.mark.asyncio
async def test_tcp_connect_success():
    writer_mock = MagicMock()
    writer_mock.close = MagicMock()
    writer_mock.wait_closed = AsyncMock()
    with patch("asyncio.open_connection", new_callable=AsyncMock, return_value=(MagicMock(), writer_mock)):
        result = await _tcp_connect("192.168.1.1", 22)
    assert result is True


@pytest.mark.asyncio
async def test_tcp_connect_timeout():
    async def timeout_open(*args, **kwargs):
        raise TimeoutError()

    with patch("asyncio.open_connection", side_effect=timeout_open):
        result = await _tcp_connect("192.168.1.1", 22)
    assert result is False


@pytest.mark.asyncio
async def test_tcp_connect_os_error():
    with patch("asyncio.open_connection", new_callable=AsyncMock, side_effect=OSError("refused")):
        result = await _tcp_connect("192.168.1.1", 9999)
    assert result is False
