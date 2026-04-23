import os
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("MCP_API_KEY", "test_key")
os.environ.setdefault("MCP_WRITE_API_KEY", "write_key")
os.environ.setdefault("BACKEND_URL", "http://testbackend")
os.environ.setdefault("AUTH_USERNAME", "admin")
os.environ.setdefault("AUTH_PASSWORD", "admin")

from app.main import app  # noqa: E402


@pytest.fixture
def api_key():
    return "test_key"


@pytest.fixture
def write_api_key():
    return "write_key"


@pytest.fixture
async def client(api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def mock_backend():
    with patch("app.resources.backend") as mock_res, \
         patch("app.tools.backend") as mock_tools:
        mock_res.get = AsyncMock()
        mock_tools.post = AsyncMock()
        mock_tools.patch = AsyncMock()
        mock_tools.delete = AsyncMock()
        yield {"resources": mock_res, "tools": mock_tools}
