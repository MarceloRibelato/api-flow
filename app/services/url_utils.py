"""
URL Utilities — URL sanitization, Docker rewriting, and domain blocking.
Extracted from flow_executor_service.py for modularity and testability.
"""
import re
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Domains to block (ads, tracking, analytics)
BLOCKED_DOMAINS = [
    "smaato.net", "temu.com", "weborama.fr", "rfihub.com",
    "doubleclick.net", "google-analytics.com", "criteo.com",
    "pubmatic.com", "adnxs.com", "rubiconproject.com", "openx.net"
]

# Internal Flow infrastructure hosts and cloud metadata endpoints (Anti-SSRF protection)
INTERNAL_PROTECTED_HOSTS = {
    "db",
    "flow-db",
    "redis",
    "flow-redis",
    "pgbouncer",
    "flow-pgbouncer",
    "license-manager",
    "flow-license-manager",
    "169.254.169.254",
    "metadata.google.internal",
}


def sanitize_url_for_docker(url: str) -> str:
    """
    Rewrites localhost URLs to use internal gateway or specific overrides when running inside Docker.
    """
    if not url:
        return url
    from app.config import settings

    # Rule A: User-specified global replacement for localhost
    if settings.TARGET_URL_REPLACEMENT and ("localhost" in url or "127.0.0.1" in url):
        new_url = re.sub(r'(https?://)(localhost|127\.0\.0\.1)', rf'\1{settings.TARGET_URL_REPLACEMENT}', url)
        logger.info(f"      🔧 Rewrote URL (Global Override): {url} -> {new_url}")
        return new_url

    # Rule B: Standard Internal rewrite for Gateway/Nginx
    if "localhost" in url or "127.0.0.1" in url:
        # Keep backend-targeted requests internal
        if ":8000" in url:
            return url.replace("localhost", "127.0.0.1")

        # Otherwise, assume it's the frontend
        gateway = settings.INTERNAL_GATEWAY_URL.rstrip('/')
        new_url = re.sub(r'https?://(localhost|127\.0\.0\.1)(:\d+)?', gateway, url)
        if new_url != url:
            logger.info(f"      🔧 Rewrote URL for Docker: {url} -> {new_url}")
            return new_url

    return url


def is_internal_protected_url(url: str) -> bool:
    """Checks if a URL targets internal Flow infrastructure containers or cloud metadata endpoints (SSRF protection)."""
    if not url:
        return False
    try:
        candidate = url if "://" in url else f"http://{url}"
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").strip().lower()
        return hostname in INTERNAL_PROTECTED_HOSTS
    except Exception:
        return False


def is_blocked_domain(url: str) -> bool:
    """Checks if a URL belongs to a blocked domain (ads, tracking, or internal protected SSRF targets)."""
    if not url:
        return False
    if is_internal_protected_url(url):
        return True
    return any(domain in url for domain in BLOCKED_DOMAINS)


def ensure_absolute_url(url: str, base_url: str = None) -> str:
    """
    Ensures the URL is absolute. If it starts with '/', prepends the API base URL.
    """
    if url and url.startswith('/'):
        if not base_url:
            from app.config import settings
            base_url = settings.API_BASE_URL
        base_url = base_url.rstrip('/')
        path = url.lstrip('/')
        return f"{base_url}/{path}"
    return url


def ensure_protocol(url: str) -> str:
    """Ensures URL has a protocol prefix."""
    if url and not url.startswith(('http://', 'https://')):
        return f"http://{url}"
    return url
