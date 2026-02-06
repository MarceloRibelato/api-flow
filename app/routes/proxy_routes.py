from fastapi import APIRouter, HTTPException, Response
import requests
from app.schemas.proxy_schemas import ProxyRequest
import logging

router = APIRouter(prefix="/proxy", tags=["Proxy"])
logger = logging.getLogger(__name__)

@router.post("/")
async def proxy_request(req: ProxyRequest):
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

        resp = requests.request(
            method=req.method,
            url=req.url,
            headers=headers,
            params=req.params,
            json=json_body,
            data=data_body,
            timeout=60
        )
        
        # Convert requests.Response to FastAPI Response
        # We want to return the exact status, headers and body
        
        # Filter response headers
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        resp_headers = {
            k: v for k, v in resp.headers.items() 
            if k.lower() not in excluded_headers
        }

        content = resp.content
        
        return Response(
            content=content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type", "application/json") 
        )

    except requests.RequestException as e:
        logger.error(f"Proxy Error: {e}")
        raise HTTPException(status_code=502, detail=f"Proxy Error: {str(e)}")
    except Exception as e:
        logger.error(f"Internal Proxy Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
