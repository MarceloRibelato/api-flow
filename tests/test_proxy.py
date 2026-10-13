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

def test_proxy_blocks_cloud_metadata_ip(client):
    payload = { "method": "GET", "url": "http://169.254.169.254/latest/meta-data/" }
    response = client.post("/proxy/", json=payload)
    assert response.status_code == 403
    assert "SSRF Protection" in response.json()["detail"]

def test_proxy_blocks_cloud_metadata_hostname(client):
    payload = { "method": "GET", "url": "http://metadata.google.internal/computeMetadata/v1/" }
    response = client.post("/proxy/", json=payload)
    assert response.status_code == 403
    assert "SSRF Protection" in response.json()["detail"]

def test_proxy_blocks_disallowed_scheme(client):
    payload = { "method": "GET", "url": "file:///etc/passwd" }
    response = client.post("/proxy/", json=payload)
    assert response.status_code == 400
    assert "Protocol 'file' is not allowed" in response.json()["detail"]

