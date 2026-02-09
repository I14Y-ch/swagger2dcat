import requests
import json
import os
import time
import pickle
import logging

# Get logger
logger = logging.getLogger('swagger2dcat')

# Constants
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
AGENTS_CACHE_FILE = os.path.join(CACHE_DIR, 'agents_cache.pkl')
CACHE_EXPIRY = 24 * 60 * 60  # 24 hours in seconds

def get_agents(fetch_details=False):
    """
    Fetch agents from the I14Y Admin API with optional detail enrichment.
    Uses local disk cache to avoid repeated API calls.
    
    Args:
        fetch_details (bool): Whether to fetch detailed information for each agent
        
    Returns:
        list: List of agent dictionaries with id, name, and optionally address
    """
    # Try to load from cache first
    cached_agents = _load_agents_from_cache()
    if cached_agents:
        logger.info(f"Returning {len(cached_agents)} agents from cache")
        return cached_agents
    
    try:
        # Fetch agents from I14Y API
        logger.info("Fetching agents from I14Y API: https://input-backend.i14y.c.bfs.admin.ch/api/Agent")
        response = requests.get('https://input-backend.i14y.c.bfs.admin.ch/api/Agent', timeout=10)
        logger.info(f"I14Y API response status: {response.status_code}")
        response.raise_for_status()
        agents_data = response.json()

        # Process agents to include display name and address
        processed_agents = []
        for agent in agents_data:
            # Skip agents without id or name
            if not agent.get('id') or not agent.get('name'):
                continue

            # Use English name if available, otherwise German, or any available language
            display_name = None
            if 'en' in agent['name'] and agent['name']['en']:
                display_name = agent['name']['en']
            elif 'de' in agent['name'] and agent['name']['de']:
                display_name = agent['name']['de']
            else:
                # Use first non-empty name value
                for lang, name in agent['name'].items():
                    if name:
                        display_name = name
                        break

            # Skip if no display name could be found
            if not display_name:
                continue

            # Initialize contact/address data
            contact_info = None

            # Only fetch detailed information if explicitly requested
            if fetch_details and agent.get('id'):
                try:
                    # Get detailed agent information directly from I14Y API
                    detail_response = requests.get(
                        f"https://input-backend.i14y.c.bfs.admin.ch/api/Agent/{agent['id']}",
                        timeout=5
                    )
                    detail_response.raise_for_status()
                    agent_details = detail_response.json()
                    
                    # Extract contact information if available
                    if agent_details.get('contactPoint'):
                        cp = agent_details['contactPoint']
                        contact_info = {
                            'address': cp.get('hasAddress', {}).get('en', ''),
                            'email': cp.get('hasEmail', ''),
                            'phone': cp.get('hasTelephone', ''),
                            'homepage': agent_details.get('homePage', '')
                        }
                except Exception as e:
                    # If I14Y API fails, we could fall back to Staatskalender API
                    # but for now we'll just continue without the detailed info
                    pass

            processed_agents.append({
                'id': agent['id'],
                'display_name': display_name,
                'name': agent['name'],  # Keep full name dictionary for reference
                'contact_info': contact_info  # Include contact info if available
            })

        # Sort agents by display name
        processed_agents.sort(key=lambda x: x['display_name'])
        
        logger.info(f"Successfully processed {len(processed_agents)} agents from I14Y API")
        
        # Save to cache
        _save_agents_to_cache(processed_agents)
        
        return processed_agents

    except Exception as e:
        # Log the error with details
        logger.error(f"Error fetching agents from I14Y API: {str(e)}", exc_info=True)
        
        # If error occurs, try to return cached data even if expired
        expired_cache = _load_agents_from_cache(ignore_expiry=True)
        if expired_cache:
            logger.info(f"Returning {len(expired_cache)} agents from expired cache")
            return expired_cache
        
        logger.warning("No cached agents available, returning empty list")
        return []

def _load_agents_from_cache(ignore_expiry=False):
    """
    Load agents data from local cache if available and not expired
    
    Args:
        ignore_expiry (bool): Whether to ignore cache expiration time
        
    Returns:
        list: Cached agents data or None if not available/expired
    """
    try:
        # Create cache directory if it doesn't exist
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        # Check if cache file exists
        if not os.path.exists(AGENTS_CACHE_FILE):
            return None
        
        # Check if cache is expired (unless we're ignoring expiry)
        if not ignore_expiry:
            cache_mtime = os.path.getmtime(AGENTS_CACHE_FILE)
            if time.time() - cache_mtime > CACHE_EXPIRY:
                return None
        
        # Load and return cached data
        with open(AGENTS_CACHE_FILE, 'rb') as f:
            return pickle.load(f)
    
    except Exception as e:
        # If any error occurs, return None to indicate cache miss
        return None

def _save_agents_to_cache(agents_data):
    """
    Save agents data to local cache
    
    Args:
        agents_data (list): List of agent dictionaries to cache
    """
    try:
        # Create cache directory if it doesn't exist
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        # Save data to cache file
        with open(AGENTS_CACHE_FILE, 'wb') as f:
            pickle.dump(agents_data, f)
        
        logger.info(f"Saved {len(agents_data)} agents to cache")
    
    except Exception as e:
        # Log cache write failures - not critical but useful for debugging
        logger.warning(f"Failed to save agents to cache: {str(e)}")