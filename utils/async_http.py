"""
Asynchronous HTTP utility functions for making parallel requests
"""
import asyncio
import httpx
from typing import Dict, List, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

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
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            return url, response.text, response.status_code
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
