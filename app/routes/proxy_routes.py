from fastapi import APIRouter, HTTPException, Response, Request
from app.schemas.proxy_schemas import ProxyRequest
import logging
import httpx 
import re

router = APIRouter(prefix="/proxy", tags=["Proxy"])
logger = logging.getLogger(__name__)

@router.post("/")
async def proxy_request(req: ProxyRequest, request: Request):
    try:
        # Prepare headers
        headers = req.headers or {}
        keys_to_remove = ['host', 'Host', 'origin', 'Origin', 'referer', 'Referer']
        for key in keys_to_remove:
            if key in headers:
                del headers[key]

        # 3. Dynamic URL Replacement
        target_url = req.url
        
        # Shortcut: Bypass Nginx if targeting our own API (backend)
        if "localhost:8000" in target_url or "127.0.0.1:8000" in target_url:
            target_url = target_url.replace("localhost:8000", "backend:8000").replace("127.0.0.1:8000", "backend:8000")
        elif "/api/" in target_url and any(h in target_url for h in ["localhost", "127.0.0.1", "flow-frontend"]):
            # Preserve the rest of the path after /api/
            api_match = re.search(r'/api/(.*)', target_url)
            if api_match:
                target_url = f"http://backend:8000/{api_match.group(1)}"
        elif "localhost" in target_url or "127.0.0.1" in target_url:
            # Better localhost handling for Docker on Windows:
            # 1. Use host.docker.internal to reach services on the Windows host
            # 2. Preserve ports (e.g., localhost:5000 -> host.docker.internal:5000)
            target_url = re.sub(r'https?://(localhost|127\.0\.0\.1)(:\d+)?', 
                               lambda m: f"http://host.docker.internal{m.group(2) if m.group(2) else ''}", 
                               target_url)
        elif "flow-frontend" in target_url:
             target_url = target_url.replace("flow-frontend", "backend:8000")

        # Use Global HTTP Client from app state
        client = request.app.state.http_client
        
        # Handling Body
        json_body = req.body if isinstance(req.body, (dict, list)) else None
        data_body = req.body if req.body and not isinstance(req.body, (dict, list)) else None

        # PREVENT STRIPPING: Only pass params if they are not empty
        kwargs = {
            "method": req.method,
            "url": target_url,
            "headers": headers,
            "json": json_body,
            "content": data_body,
            "timeout": 30.0
        }
        
        if req.params and len(req.params) > 0:
            kwargs["params"] = req.params

        logger.info(f"PROXY: {req.method} {req.url} -> {target_url}")
        
        resp = await client.request(**kwargs)
        
        # Filter response headers
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        resp_headers = {
            k: v for k, v in resp.headers.items() 
            if k.lower() not in excluded_headers
        }

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type", "application/json") 
        )

    except httpx.RequestError as e:
        logger.error(f"Proxy Error: {e}")
        raise HTTPException(status_code=502, detail=f"Proxy Error: {str(e)}")
    except Exception as e:
        logger.error(f"Internal Proxy Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
