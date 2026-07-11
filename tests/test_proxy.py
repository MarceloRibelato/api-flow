import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

def test_proxy_basic(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"success": true}'
    mock_resp.headers = {"Content-Type": "application/json"}
    
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_resp
        
        payload = {
            "method": "GET",
            "url": "https://api.external.com/v1/test",
            "headers": {"X-Test": "Value"},
            "params": {"q": "search"}
        }

        response = client.post("/proxy/", json=payload)
        # If response.status_code is 500, it means the AttributeError happened
        assert response.status_code == 200
        assert response.json() == {"success": True}
        mock_request.assert_called_once()

def test_proxy_error_handling(client):
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = httpx.RequestError("Connection failed")
        
        payload = { "method": "GET", "url": "https://broken.api" }
        response = client.post("/proxy/", json=payload)
        
        assert response.status_code == 502
        assert "Proxy Error" in response.json()["detail"]
