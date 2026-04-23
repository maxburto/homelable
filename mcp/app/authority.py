from __future__ import annotations

from contextvars import ContextVar, Token


READONLY_AUTHORITY = "readonly"
WRITE_AUTHORITY = "write"

_current_authority: ContextVar[str] = ContextVar(
    "homelable_mcp_authority",
    default=READONLY_AUTHORITY,
)


def get_current_authority() -> str:
    return _current_authority.get()


def set_current_authority(authority: str) -> Token[str]:
    return _current_authority.set(authority)


def reset_current_authority(token: Token[str]) -> None:
    _current_authority.reset(token)
