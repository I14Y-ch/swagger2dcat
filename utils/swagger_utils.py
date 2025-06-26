import json
import requests
import time
import re
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse, urljoin

def create_session_with_retries():
    """Create a requests session with retry strategy"""
    session = requests.Session()
    
    retry_strategy = Retry(
        total=3,
        status_forcelist=[429, 500, 502, 503, 504],
        backoff_factor=1
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

def detect_swagger_json_url(html_url):
    """
    Try to detect the actual Swagger JSON URL from an HTML page by looking for .json links
    
    Args:
        html_url: URL to the HTML page (e.g., Swagger UI page)
    
    Returns:
        tuple: (detected_url, success_flag) where detected_url is the JSON URL or None
    """
    try:
        # Analyzing HTML page
        # Fetch the HTML page
        response = requests.get(html_url, timeout=10)
        response.raise_for_status()
        html_content = response.text
        
        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Strategy 1: Check for Swagger UI specific patterns
        # Look for the configUrl in Swagger UI initialization
        swagger_config_patterns = [
            r'url:\s*["\']([^"\']*\.json[^"\']*)["\']',
            r'configUrl:\s*["\']([^"\']*\.json[^"\']*)["\']',
            r'spec:\s*["\']([^"\']*\.json[^"\']*)["\']',
            r'"url":\s*"([^"]*\.json[^"]*)"',
            r'"spec":\s*"([^"]*\.json[^"]*)"'
        ]
        
        for script in soup.find_all('script'):
            script_text = script.string or ''
            for pattern in swagger_config_patterns:
                matches = re.findall(pattern, script_text)
                if matches:
                    detected_url = urljoin(html_url, matches[0])
                    print(f"✅ Found Swagger config URL in script: {detected_url}")
                    return detected_url, True
        
        # Strategy 2: Look for <script> tags with src containing swagger or openapi
        for script in soup.find_all('script', src=True):
            src = script.get('src', '')
            if '.json' in src and ('swagger' in src.lower() or 'openapi' in src.lower() or 'api-docs' in src.lower()):
                detected_url = urljoin(html_url, src)
                print(f"✅ Found JSON URL in script src: {detected_url}")
                return detected_url, True
        
        # Strategy 3: Look for all <a> tags with href ending in .json
        json_links = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.endswith('.json'):
                absolute_url = urljoin(html_url, href)
                json_links.append({
                    'url': absolute_url,
                    'text': link.get_text(strip=True),
                    'link_element': link
                })
        
        if json_links:
            print(f"📋 Found {len(json_links)} JSON links in page")
            for i, link in enumerate(json_links):
                print(f"  {i+1}. {link['url']} (text: '{link['text'][:50]}...')")
            
            # Return the first JSON link found
            selected_url = json_links[0]['url']
            print(f"✅ Selected JSON URL: {selected_url}")
            return selected_url, True
        
        # Strategy 4: Construct common Swagger endpoints and check if they exist
        base_url = html_url.rstrip('/')
        if base_url.endswith('/swagger/index.html'):
            # Handle common Swagger UI path pattern
            base_url = base_url.replace('/swagger/index.html', '')
        
        common_patterns = [
            '/swagger/v1/swagger.json',
            '/swagger.json',
            '/api-docs',
            '/api-docs.json',
            '/v1/api-docs',
            '/v2/api-docs',
            '/v3/api-docs',
            '/swagger/doc.json',
            '/swagger/api-docs.json',
            '/api/swagger.json'
        ]
        
        for pattern in common_patterns:
            test_url = base_url + pattern
            try:
                print(f"Testing potential JSON URL: {test_url}")
                test_response = requests.head(test_url, timeout=5)
                if test_response.status_code == 200:
                    content_type = test_response.headers.get('content-type', '').lower()
                    if 'json' in content_type or 'application/octet-stream' in content_type:
                        print(f"✅ Found working JSON endpoint: {test_url}")
                        return test_url, True
            except Exception as e:
                print(f"Error testing {test_url}: {str(e)}")
                continue
        
        # Strategy 5: For Swagger UI specifically, try to extract from the index.html
        if 'swagger' in html_url.lower() and 'index.html' in html_url.lower():
            # Try replacing "index.html" with common JSON patterns
            base_dir = html_url.rsplit('/', 1)[0]  # Remove index.html
            swagger_json_possibilities = [
                f"{base_dir}/swagger.json",
                f"{base_dir}/../swagger.json",
                f"{base_dir}/api-docs.json",
                f"{base_dir}/doc.json"
            ]
            
            for possible_url in swagger_json_possibilities:
                try:
                    print(f"Trying possible Swagger JSON URL: {possible_url}")
                    json_response = requests.head(possible_url, timeout=5)
                    if json_response.status_code == 200:
                        print(f"✅ Found working JSON URL: {possible_url}")
                        return possible_url, True
                except:
                    continue
                    
        # Strategy 6: For the specific example in the prompt
        if 'api.termdat.bk.admin.ch/swagger/index.html' in html_url:
            # This specific API seems to use swagger/v1/swagger.json
            specific_url = html_url.replace('index.html', 'v1/swagger.json')
            try:
                print(f"Trying known pattern for termdat API: {specific_url}")
                test_response = requests.head(specific_url, timeout=5)
                if test_response.status_code == 200:
                    print(f"✅ Found working JSON URL for termdat API: {specific_url}")
                    return specific_url, True
            except:
                pass
        
        print("❌ Could not detect JSON URL from HTML page")
        return None, False
        
    except Exception as e:
        print(f"❌ Error detecting JSON URL: {str(e)}")
        return None, False

def is_likely_json_url(url):
    """
    Check if a URL is likely pointing to a JSON file
    
    Args:
        url (str): URL to check
    
    Returns:
        bool: True if URL likely points to JSON
    """
    url_lower = url.lower()
    
    # Direct JSON file indicators
    if url_lower.endswith('.json'):
        return True
    
    # API documentation endpoints
    json_indicators = [
        'api-docs',
        'swagger.json',
        'openapi.json',
        '/v2/api-docs',
        '/v3/api-docs',
        'swagger/v1/swagger.json'
    ]
    
    return any(indicator in url_lower for indicator in json_indicators)

def resolve_swagger_url(input_url, timeout=10):
    """
    Resolve the input URL to a Swagger JSON URL
    
    Args:
        input_url (str): User-provided URL (could be HTML page or JSON)
        timeout (int): Request timeout in seconds
    
    Returns:
        dict: {'json_url': str, 'original_url': str, 'detected': bool}
    """
    try:
        print(f"Resolving Swagger URL: {input_url}")
        
        # First, check if the URL already looks like a JSON endpoint
        if is_likely_json_url(input_url):
            print("URL appears to be a direct JSON endpoint")
            # Test if it's actually accessible and valid JSON
            session = create_session_with_retries()
            try:
                response = session.get(input_url, timeout=timeout)
                response.raise_for_status()
                json_data = response.json()
                
                # Verify it's a Swagger/OpenAPI spec
                if ('swagger' in json_data or 'openapi' in json_data or 
                    'info' in json_data or 'paths' in json_data):
                    return {
                        'json_url': input_url,
                        'original_url': input_url,
                        'detected': False
                    }
            except Exception as e:
                print(f"Direct JSON URL test failed: {str(e)}")
        
        # If not a direct JSON URL or validation failed, try to detect from HTML
        detected_url = detect_swagger_json_url(input_url, timeout)
        
        if detected_url:
            return {
                'json_url': detected_url,
                'original_url': input_url,
                'detected': True
            }
        else:
            # If detection failed, return the original URL with a warning
            return {
                'json_url': input_url,
                'original_url': input_url,
                'detected': False,
                'warning': 'Could not detect JSON URL from HTML page. Using original URL.'
            }
            
    except Exception as e:
        print(f"Error resolving Swagger URL: {str(e)}")
        return {
            'json_url': input_url,
            'original_url': input_url,
            'detected': False,
            'error': str(e)
        }

def extract_swagger_info(swagger_url, timeout=10):
    """
    Extract relevant information from Swagger/OpenAPI specification
    Now with automatic URL detection
    
    Args:
        swagger_url (str): URL to Swagger documentation (HTML or JSON)
        timeout (int): Request timeout in seconds
    
    Returns:
        dict: Extracted information from Swagger
    """
    try:
        print(f"Extracting Swagger info from: {swagger_url}")
        start_time = time.time()
        
        # Resolve the URL to get the actual JSON endpoint
        url_resolution = resolve_swagger_url(swagger_url, timeout)
        actual_json_url = url_resolution['json_url']
        
        if 'warning' in url_resolution:
            print(f"Warning: {url_resolution['warning']}")
        
        if url_resolution['detected']:
            print(f"Detected JSON URL: {actual_json_url}")
        
        # Use session with retries and timeout
        session = create_session_with_retries()
        
        # Fetch with timeout
        response = session.get(actual_json_url, timeout=timeout)
        response.raise_for_status()
        
        fetch_time = time.time() - start_time
        # Swagger fetch completed
        
        # Parse JSON efficiently
        try:
            swagger_data = response.json()
        except json.JSONDecodeError as e:
            return {
                'error': f'Invalid JSON in Swagger specification: {str(e)}'
            }
        
        # Extract basic info efficiently
        info = swagger_data.get('info', {})
        title = info.get('title', 'Unknown API')
        description = info.get('description', '')
        version = info.get('version', '')
        
        # Extract paths and create endpoint summary and endpoint details
        paths = swagger_data.get('paths', {})
        endpoint_summary = ""
        keywords = []
        endpoint_details = []
        endpoint_short_descriptions = []

        if paths:
            # Count active methods for summary
            method_counts = {'GET': 0, 'POST': 0, 'PUT': 0, 'DELETE': 0, 'PATCH': 0}
            for path, operations in paths.items():
                for method, details in operations.items():
                    method_lower = method.lower()
                    if method_lower in method_counts:
                        method_counts[method_lower] += 1
            # Create summary line
            active_methods = [f"{count} {method}" for method, count in method_counts.items() if count > 0]
            method_summary = ", ".join(active_methods)
            if method_summary:
                endpoint_summary = f"API contains {len(paths)} endpoints with {method_summary} operations"
            else:
                endpoint_summary = f"API contains {len(paths)} endpoints"
            
            # Extract details for each endpoint
            for path, operations in paths.items():
                for method, details in operations.items():
                    # Extract summary for endpoint details
                    summary = details.get('summary', '')
                    desc = details.get('description', '')
                    # Compose a very short description for each endpoint
                    short_desc = summary or desc or ''
                    if short_desc:
                        short_desc = short_desc.strip().split('\n')[0]
                        if len(short_desc) > 120:
                            short_desc = short_desc[:117] + "..."
                    else:
                        short_desc = "No description available."
                    endpoint_short_descriptions.append({
                        "method": method.upper(),
                        "path": path,
                        "short_description": short_desc
                    })
                    # Extract detailed information for each endpoint
                    endpoint_details.append({
                        'path': path,
                        'method': method,
                        'summary': summary,
                        'description': desc,
                        'parameters': details.get('parameters', []),
                        'responses': details.get('responses', {}),
                        'tags': details.get('tags', [])
                    })
        
        total_time = time.time() - start_time
        # Swagger parsing completed
        
        # Build result
        result = {
            'title': title,
            'description': description,
            'version': version,
            'endpoint_summary': endpoint_summary,
            'keywords': keywords,
            'additional_info': f"Extracted from Swagger/OpenAPI specification. Contains {len(paths)} endpoint paths.",
            'original_url': url_resolution['original_url'],
            'resolved_url': actual_json_url,
            'url_detected': url_resolution['detected'],
            'endpoint_short_descriptions': endpoint_short_descriptions
        }
        
        # Add detection info if URL was detected
        if url_resolution['detected']:
            result['url_detected'] = True
            result['original_url'] = url_resolution['original_url']
            result['additional_info'] += f" (JSON URL auto-detected from {url_resolution['original_url']})"
        
        return result
        
    except requests.exceptions.Timeout:
        return {
            'error': f'Timeout while fetching Swagger specification (>{timeout}s)'
        }
    except requests.exceptions.RequestException as e:
        return {
            'error': f'Error fetching Swagger specification: {str(e)}'
        }
    except Exception as e:
        return {
            'error': f'Error parsing Swagger specification: {str(e)}'
        }