"""Per-node status checks: ping, http, https, tcp, ssh, prometheus, health, Proxmox, none."""
import asyncio
import ipaddress
import logging
import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

PROXMOX_METHODS = {
    "proxmox-vm": "qemu",
    "proxmox-lxc": "lxc",
}

SKIP_GUEST_IP_PREFIXES = ("lo", "docker", "br-", "veth", "tap", "tun")


class ProxmoxConfigError(ValueError):
    """A Proxmox-backed check is missing safe runtime configuration."""


async def check_node(check_method: str, target: str | None, ip: str | None) -> dict[str, Any]:
    """
    Run the appropriate check and return {status, response_time_ms}.
    status is one of: online, offline, unknown.
    """
    if check_method == "none":
        return {"status": "online", "response_time_ms": None}

    # Use only the first IP when the field contains comma-separated addresses
    raw_ip = ip.split(",")[0].strip() if ip else None
    host = target or raw_ip
    if not host:
        return {"status": "unknown", "response_time_ms": None}

    start = time.monotonic()
    try:
        if check_method in PROXMOX_METHODS:
            result = await _proxmox_guest_check(check_method, host, raw_ip)
            result["response_time_ms"] = int((time.monotonic() - start) * 1000)
            return result

        match check_method:
            case "ping":
                ok = await _ping(host)
            case "http":
                url = host if host.startswith("http") else f"http://{host}"
                ok = await _http_get(url)
            case "https":
                url = host if host.startswith("https") else f"https://{host}"
                ok = await _http_get(url, verify=True)
            case "tcp":
                host_part, _, port_str = host.rpartition(":")
                port = int(port_str) if port_str.isdigit() else 80
                ok = await _tcp_connect(host_part or host, port)
            case "ssh":
                ok = await _tcp_connect(host, 22)
            case "prometheus":
                url = host if host.startswith("http") else f"http://{host}/metrics"
                ok = await _http_get(url)
            case "health":
                url = host if host.startswith("http") else f"http://{host}/health"
                ok = await _http_get(url)
            case _:
                ok = await _ping(host)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {"status": "online" if ok else "offline", "response_time_ms": elapsed_ms}

    except ProxmoxConfigError as exc:
        logger.debug("Proxmox check is not configured for %s: %s", host, exc)
        return {"status": "unknown", "response_time_ms": None}
    except Exception as exc:
        logger.debug("Check failed for %s (%s): %s", host, check_method, exc)
        return {"status": "offline", "response_time_ms": None}


async def _ping(host: str) -> bool:
    if sys.platform == "win32":
        args = ["ping", "-n", "1", "-w", "1000", host]
    else:
        args = ["ping", "-c", "1", "-W", "1", host]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


async def _http_get(url: str, verify: bool = False) -> bool:
    async with httpx.AsyncClient(verify=verify, timeout=5) as client:
        resp = await client.get(url, follow_redirects=True)
        return resp.status_code < 500


async def _tcp_connect(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (TimeoutError, OSError, socket.gaierror):
        return False


async def _proxmox_guest_check(check_method: str, target: str, current_ip: str | None) -> dict[str, Any]:
    profile_name, resource, guest_id = _parse_proxmox_target(check_method, target)
    profile = _get_proxmox_profile(profile_name)
    node_name = str(profile.get("node") or profile_name)

    status_payload = await _proxmox_api_get(
        profile,
        f"nodes/{node_name}/{resource}/{guest_id}/status/current",
    )
    status_data = _payload_data(status_payload)
    if not isinstance(status_data, dict):
        raise ProxmoxConfigError("Proxmox status response was not an object")

    runtime_state = str(status_data.get("status") or "unknown")
    status = _runtime_status(runtime_state)
    properties = _runtime_properties(profile_name, resource, guest_id, status_data)
    result: dict[str, Any] = {
        "status": status,
        "properties": properties,
    }

    if resource == "qemu" and status == "online":
        guest_details = await _proxmox_qga_details(profile, node_name, guest_id, current_ip)
        result.update({k: v for k, v in guest_details.items() if k != "properties" and v})
        result["properties"] = _merge_properties(properties, guest_details.get("properties", []))

    return result


def _parse_proxmox_target(check_method: str, target: str) -> tuple[str, str, str]:
    parsed = urlparse(target)
    if parsed.scheme != "proxmox" or not parsed.netloc:
        raise ProxmoxConfigError("target must use proxmox://<profile>/<qemu|lxc>/<id>")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ProxmoxConfigError("target must include a resource type and numeric guest id")

    resource, guest_id = parts
    expected_resource = PROXMOX_METHODS[check_method]
    if resource != expected_resource:
        raise ProxmoxConfigError(
            f"method {check_method} requires resource {expected_resource}, got {resource}"
        )
    if not guest_id.isdigit():
        raise ProxmoxConfigError("guest id must be numeric")
    return parsed.netloc, resource, guest_id


def _get_proxmox_profile(profile_name: str) -> dict[str, Any]:
    profile = settings.proxmox_api_profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise ProxmoxConfigError(f"missing Proxmox API profile {profile_name!r}")

    base_url = str(profile.get("base_url") or "").rstrip("/")
    token_id = str(profile.get("token_id") or "")
    token_secret = str(profile.get("token_secret") or "")
    if not base_url or not token_id or not token_secret:
        raise ProxmoxConfigError(f"Proxmox API profile {profile_name!r} is incomplete")

    return {
        **profile,
        "base_url": base_url,
        "token_id": token_id,
        "token_secret": token_secret,
        "verify_ssl": _config_bool(profile.get("verify_ssl", True)),
        "timeout_seconds": float(profile.get("timeout_seconds", 5)),
    }


async def _proxmox_api_get(profile: dict[str, Any], path: str) -> dict[str, Any]:
    url = f"{profile['base_url']}/api2/json/{path.lstrip('/')}"
    headers = {
        "Authorization": f"PVEAPIToken={profile['token_id']}={profile['token_secret']}",
    }
    async with httpx.AsyncClient(
        verify=profile["verify_ssl"],
        timeout=profile["timeout_seconds"],
    ) as client:
        response = await client.get(url, headers=headers)
        if response.status_code in {401, 403}:
            raise ProxmoxConfigError("Proxmox API token was rejected")
        response.raise_for_status()
        return response.json()


async def _proxmox_qga_details(
    profile: dict[str, Any],
    node_name: str,
    guest_id: str,
    current_ip: str | None,
) -> dict[str, Any]:
    base_path = f"nodes/{node_name}/qemu/{guest_id}/agent"
    try:
        await _proxmox_api_get(profile, f"{base_path}/ping")
    except Exception:
        return {
            "properties": [_property("Guest Agent", "unavailable", "Shield", visible=True)],
        }

    properties = [_property("Guest Agent", "online", "Shield", visible=True)]
    details: dict[str, Any] = {}

    network_payload = await _proxmox_best_effort_get(profile, f"{base_path}/network-get-interfaces")
    interfaces = _agent_result(network_payload)
    if isinstance(interfaces, list):
        guest_ip = _select_guest_ipv4(interfaces, current_ip)
        if guest_ip:
            details["ip"] = guest_ip
            properties.append(_property("Guest IP", guest_ip, "Network", visible=True))

    hostname_payload = await _proxmox_best_effort_get(profile, f"{base_path}/get-host-name")
    hostname_result = _agent_result(hostname_payload)
    if isinstance(hostname_result, dict):
        hostname = hostname_result.get("host-name") or hostname_result.get("hostname")
        if hostname:
            details["hostname"] = str(hostname)
            properties.append(_property("Guest Hostname", str(hostname), "Server", visible=False))

    os_payload = await _proxmox_best_effort_get(profile, f"{base_path}/get-osinfo")
    os_result = _agent_result(os_payload)
    if isinstance(os_result, dict):
        os_name = os_result.get("pretty-name") or os_result.get("name")
        if os_name:
            properties.append(_property("Guest OS", str(os_name), "Monitor", visible=False))

    details["properties"] = properties
    return details


async def _proxmox_best_effort_get(profile: dict[str, Any], path: str) -> dict[str, Any] | None:
    try:
        return await _proxmox_api_get(profile, path)
    except Exception:
        return None


def _payload_data(payload: dict[str, Any] | None) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _agent_result(payload: dict[str, Any] | None) -> Any:
    data = _payload_data(payload)
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return data


def _runtime_status(runtime_state: str) -> str:
    normalized = runtime_state.lower()
    if normalized == "running":
        return "online"
    if normalized in {"stopped", "paused", "suspended"}:
        return "offline"
    return "unknown"


def _runtime_properties(
    profile_name: str,
    resource: str,
    guest_id: str,
    status_data: dict[str, Any],
) -> list[dict[str, Any]]:
    label = "VMID" if resource == "qemu" else "CTID"
    properties = [
        _property("Proxmox Profile", profile_name, "Server", visible=False),
        _property(label, str(guest_id), "Hash", visible=True),
        _property("Runtime State", str(status_data.get("status") or "unknown"), "Zap", visible=True),
    ]
    name = status_data.get("name")
    if name:
        properties.append(_property("Proxmox Name", str(name), "Tag", visible=False))
    return properties


def _property(key: str, value: str, icon: str | None = None, visible: bool = True) -> dict[str, Any]:
    return {"key": key, "value": value, "icon": icon, "visible": visible}


def _merge_properties(
    base: list[dict[str, Any]],
    updates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged = list(base)
    for update in updates or []:
        key = str(update.get("key") or "").casefold()
        if not key:
            continue
        for index, existing in enumerate(merged):
            if str(existing.get("key") or "").casefold() == key:
                merged[index] = update
                break
        else:
            merged.append(update)
    return merged


def _select_guest_ipv4(interfaces: list[dict[str, Any]], current_ip: str | None) -> str | None:
    candidates: list[tuple[int, str]] = []
    for interface in interfaces:
        name = str(interface.get("name") or "")
        addresses = interface.get("ip-addresses") or []
        if not isinstance(addresses, list):
            continue
        for address in addresses:
            if not isinstance(address, dict):
                continue
            raw_address = str(address.get("ip-address") or "")
            if address.get("ip-address-type") != "ipv4" or not _usable_guest_ip(raw_address):
                continue
            if current_ip and raw_address == current_ip:
                return raw_address
            priority = 1
            if name.startswith(SKIP_GUEST_IP_PREFIXES):
                priority += 10
            try:
                if not ipaddress.ip_address(raw_address).is_private:
                    priority += 5
            except ValueError:
                continue
            candidates.append((priority, raw_address))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _usable_guest_ip(raw_address: str) -> bool:
    try:
        address = ipaddress.ip_address(raw_address)
    except ValueError:
        return False
    return not (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    )


def _config_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)
