from fastapi import APIRouter, HTTPException, Response, Request
from app.schemas.proxy_schemas import ProxyRequest
import logging
import httpx 

router = APIRouter(prefix="/proxy", tags=["Proxy"])
logger = logging.getLogger(__name__)

@router.post("/")
async def proxy_request(req: ProxyRequest, request: Request):
    try:
        # Prepare headers (filter out unsafe ones if necessary, but for dev tool we want transparency)
        headers = req.headers or {}
        
        # Remove host, origin, and referer headers to avoid conflicts and CORS issues
        # (captured requests often contain these, causing 403s on replay)
        keys_to_remove = ['host', 'Host', 'origin', 'Origin', 'referer', 'Referer']
        for key in keys_to_remove:
            if key in headers:
                del headers[key]

        # Handling Body
        json_body = None
        data_body = None
        
        if req.body:
             if isinstance(req.body, dict) or isinstance(req.body, list):
                 json_body = req.body
             else:
                 data_body = req.body

        # Rewrite localhost to flow-frontend if target is port 80 (API Gateway)
        # This fixes "connection refused" when backend tries to hit localhost:80 inside its container
        target_url = req.url
        if target_url.startswith("http://localhost/") or target_url.startswith("http://127.0.0.1/"):
             target_url = target_url.replace("http://localhost/", "http://flow-frontend/")
             target_url = target_url.replace("http://127.0.0.1/", "http://flow-frontend/")
             logger.info(f"🔄 Proxy Rewrote URL: {req.url} -> {target_url}")

        # Use Global HTTP Client from app state
        client = request.app.state.http_client
        
        resp = await client.request(
            method=req.method,
            url=target_url,
            headers=headers,
            params=req.params,
            json=json_body,
            content=data_body
        )
            
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
        # Return 502 Bad Gateway for upstream errors
        raise HTTPException(status_code=502, detail=f"Proxy Error: {str(e)}")
    except Exception as e:
        logger.error(f"Internal Proxy Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


