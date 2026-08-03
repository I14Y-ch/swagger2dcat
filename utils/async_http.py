"""
Asynchronous HTTP utility functions for making parallel requests
"""
import asyncio
import httpx
from typing import Dict, List, Any, Optional, Tuple
import logging
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


def is_safe_public_url(url: str) -> Tuple[bool, str]:
    """Return whether a URL resolves to a public IP address."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "invalid URL"

    if parsed.scheme not in {"http", "https"}:
        return False, "unsupported scheme"

    hostname = parsed.hostname
    if not hostname:
        return False, "missing hostname"

    lowered = hostname.lower()
    if lowered in {"localhost", "localhost.localdomain"}:
        return False, "localhost is not allowed"

    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False, "hostname resolution failed"

    if not infos:
        return False, "hostname resolution failed"

    for info in infos:
        ip_str = info[4][0]
        try:
            ip_addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, "invalid resolved IP"

        if (
            ip_addr.is_private
            or ip_addr.is_loopback
            or ip_addr.is_link_local
            or ip_addr.is_multicast
            or ip_addr.is_reserved
            or ip_addr.is_unspecified
        ):
            return False, f"resolved to non-public IP {ip_str}"

    return True, "ok"


async def _fetch_with_redirect_validation(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict,
    redirects_remaining: int,
) -> Tuple[str, Optional[str], int]:
    safe, reason = is_safe_public_url(url)
    if not safe:
        logger.warning(f"Blocked outbound request to unsafe URL {url}: {reason}")
        return url, None, 0

    response = await client.get(url, headers=headers, follow_redirects=False)
    if response.status_code in {301, 302, 303, 307, 308} and redirects_remaining > 0:
        location = response.headers.get("Location")
        if location:
            next_url = urljoin(url, location)
            return await _fetch_with_redirect_validation(client, next_url, headers, redirects_remaining - 1)

    return url, response.text, response.status_code

async def fetch_url(url: str, timeout: int = 10, headers: Dict = None) -> Tuple[str, Optional[str], int]:
    """
    Asynchronously fetch a URL with timeout
    
    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        headers: Optional request headers
    
    Returns:
        Tuple of (url, content, status_code)
        If the request fails, content will be None
    """
    if headers is None:
        headers = {}
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await _fetch_with_redirect_validation(client, url, headers, redirects_remaining=3)
    except Exception as e:
        logger.warning(f"Error fetching {url}: {str(e)}")
        return url, None, 0

async def fetch_multiple_urls(urls: List[str], timeout: int = 10, headers: Dict = None) -> Dict[str, Any]:
    """
    Fetch multiple URLs in parallel
    
    Args:
        urls: List of URLs to fetch
        timeout: Request timeout in seconds
        headers: Optional request headers
    
    Returns:
        Dictionary mapping URL to (content, status_code) tuples
    """
    if not urls:
        return {}
    
    tasks = [fetch_url(url, timeout, headers) for url in urls]
    results = await asyncio.gather(*tasks)
    
    return {url: (content, status_code) for url, content, status_code in results}

async def check_multiple_urls(urls: List[str], timeout: int = 5) -> Dict[str, bool]:
    """
    Check if multiple URLs are accessible in parallel
    
    Args:
        urls: List of URLs to check
        timeout: Request timeout in seconds
    
    Returns:
        Dictionary mapping URL to boolean accessibility
    """
    if not urls:
        return {}
    
    results = await fetch_multiple_urls(urls, timeout)
    return {url: (status_code >= 200 and status_code < 400) 
            for url, (_, status_code) in results.items()}

# Synchronous wrapper function for compatibility
def fetch_urls_sync(urls: List[str], timeout: int = 10, headers: Dict = None) -> Dict[str, Any]:
    """
    Synchronous wrapper for fetch_multiple_urls
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(fetch_multiple_urls(urls, timeout, headers))
    finally:
        loop.close()

def check_urls_sync(urls: List[str], timeout: int = 5) -> Dict[str, bool]:
    """
    Synchronous wrapper for check_multiple_urls
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(check_multiple_urls(urls, timeout))
    finally:
        loop.close()
