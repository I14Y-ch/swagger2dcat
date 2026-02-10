import os
import json
import logging
import requests
from openai import OpenAI

# Get logger
logger = logging.getLogger('swagger2dcat')

# Try to get API key from environment with proper fallbacks and helpful error messages
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')  # Default to GPT-4o-mini

if not OPENAI_API_KEY:
    try:
        from config import OPENAI_API_KEY
    except (ImportError, AttributeError):
        pass

# Initialize client only if API key is available, otherwise it will be initialized when needed
client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_openai_client():
    """Get OpenAI client with proper error handling"""
    global client
    if client is not None:
        return client
    
    # Try to initialize on demand
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        client = OpenAI(api_key=api_key)
        return client
    else:
        raise ValueError(
            "OpenAI API key not found. Make sure to set the OPENAI_API_KEY environment variable "
            "or provide it in a config.py file."
        )

def generate_api_description(swagger_url, landing_page_url=None, landing_page_content=None):
    """
    Generate API description using OpenAI based on swagger URL and optional landing page content
    """
    
    # Fetch Swagger content
    try:
        swagger_response = requests.get(swagger_url)
        swagger_response.raise_for_status()
        swagger_content = swagger_response.text
        
        # Parse the swagger JSON to extract only essential information
        try:
            swagger_json = json.loads(swagger_content)
            # Extract only the essential parts of the Swagger document to reduce token count
            essential_swagger = {
                "info": swagger_json.get("info", {}),
                "paths": {
                    # Take only the first 10 paths to reduce size
                    k: swagger_json.get("paths", {}).get(k, {}) 
                    for k in list(swagger_json.get("paths", {}).keys())[:10]
                }
            }
            # Add tags if present
            if "tags" in swagger_json:
                essential_swagger["tags"] = swagger_json["tags"]
                
            swagger_content = json.dumps(essential_swagger)
        except json.JSONDecodeError:
            # If parsing fails, use a truncated version of the original content
            swagger_content = swagger_content[:8000]  # Limit to approximately 8000 characters
            
    except Exception as e:
        logger.error(f"Error fetching swagger URL: {str(e)}")
        return {
            "error": "Error fetching API specification. Please check the URL."
        }
    
    # Use the pre-extracted landing page content or set to empty string if none provided
    if landing_page_content is None:
        landing_page_content = ""

    # Prepare prompt for OpenAI - emphasize detail and comprehensiveness
    system_prompt = """Create a comprehensive and detailed API description for a data catalog based on Swagger/OpenAPI docs. Focus on thoroughness and detail rather than brevity. Include technical aspects and use cases where possible."""
    
    # Extract title and description from swagger if available
    api_title = ""
    api_description = ""
    api_version = ""
    endpoint_summary = ""
    method_summary = ""
    
    try:
        swagger_json = json.loads(swagger_content)
        info = swagger_json.get("info", {})
        api_title = info.get("title", "")
        api_description = info.get("description", "")
        api_version = info.get("version", "")
        
        # Try to extract endpoint information from parsed Swagger content
        paths = swagger_json.get("paths", {})
        if paths:
            # Count HTTP methods
            method_counts = {'get': 0, 'post': 0, 'put': 0, 'delete': 0, 'patch': 0}
            endpoint_details = []
            endpoint_short_descriptions = []
            # Try to extract endpoint short descriptions if present
            if 'endpoint_short_descriptions' in swagger_json:
                endpoint_short_descriptions = swagger_json['endpoint_short_descriptions']
            else:
                # Fallback: try to extract from paths
                for path, operations in paths.items():
                    for method, details in operations.items():
                        summary = details.get('summary', '')
                        desc = details.get('description', '')
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
                    
                    for method, details in operations.items():
                        method_lower = method.lower()
                        if method_lower in method_counts:
                            method_counts[method_lower] += 1
                    
                        # Extract summary/description
                        summary = details.get('summary', '')
                        description = details.get('description', '')
                        
                        endpoint_detail = f"{method.upper()} {path}"
                        if summary:
                            endpoint_detail += f": {summary}"
                        elif description:
                            first_sentence = description.split('.')[0] + '.' if '.' in description else description
                            if len(first_sentence) > 100:
                                first_sentence = first_sentence[:100] + "..."
                            endpoint_detail += f": {first_sentence}"
                        
                        endpoint_details.append(endpoint_detail)
            
            # Create method summary
            method_summary_parts = []
            for method, count in method_counts.items():
                if count > 0:
                    method_summary_parts.append(f"{count} {method.upper()}")
            
            method_summary = ", ".join(method_summary_parts)
            
            # Create endpoint summary (limit to 30 for brevity)
            endpoint_summary = "\n".join(endpoint_details[:30])
            if len(endpoint_details) > 15:
                endpoint_summary += f"\n... and {len(endpoint_details) - 15} more endpoints"
        else:
            endpoint_short_descriptions = [{"method": "N/A", "path": "N/A", "short_description": "No endpoints available."}]
    except:
        pass
        
    # Add endpoint_short_descriptions to the prompt
    endpoint_short_desc_text = ""
    if endpoint_short_descriptions:
        endpoint_short_desc_text = "\n".join(
            [f"{ep['method']} {ep['path']}: {ep['short_description']}" for ep in endpoint_short_descriptions[:30]]
        )
        if len(endpoint_short_descriptions) > 30:
            endpoint_short_desc_text += f"\n... and {len(endpoint_short_descriptions) - 30} more endpoints"
    else:
        endpoint_short_desc_text = "No endpoint details available."

    user_prompt = f"""Based on this API information:
    
    Title: {api_title}
    Description: {api_description}
    Version: {api_version}
    
    Method Summary: {method_summary}
    
    Endpoint Details:
    {endpoint_summary}
    
    Endpoint Short Descriptions:
    {endpoint_short_desc_text}
    
    Additional Information from Landing Page:
    {landing_page_content[:1500] if landing_page_content else 'No additional information available.'}
    
    Create a detailed and precise API description with:
    1. A clear title (max 10 words)
    
    2. A DETAILED description (MINIMUM 50 words) that includes:
       - The main purpose and functionality of the API
       - What types of resources it manages or provides access to
       - What operations it allows (GET/POST/PUT/DELETE etc.) and their purposes
       - Who would use this API and for what use cases
       - Any technical or domain-specific features worth highlighting
       - DO NOT BE BRIEF - detailed descriptions are required!
    
    3. Five specific keywords that best describe the API's domain and functionality
    
    4. Between 1-3 theme codes from this list (choose the most appropriate categories):
       101=Arbeit (Work/Labor)
       102=Bauen (Construction)
       103=Bildung (Education)
       104=Aussenbeziehungen (Foreign Relations)
       105=Gerichtsbarkeit (Jurisdiction/Legal)
       106=Gesellschaft (Society)
       107=Politische Aktivitäten (Political Activities)
       108=Kultur (Culture)
       109=Landwirtschaft (Agriculture)
       110=Infrastruktur (Infrastructure)
       111=Sicherheit (Security)
       112=Steuern (Taxes)
       113=Umwelt (Environment)
       114=Gesundheit (Health)
       115=Wirtschaft (Economy)
       116=Mobilität (Mobility)
       117=Einwohner (Residents/Citizens)
       118=Unternehmen (Business/Companies)
       119=Behörden (Public Authorities)
       120=Gebäude und Grundstücke (Buildings/Properties)
       121=Tiere (Animals)
       122=Geoinformationen (Geo-information)
       123=Rechtssammlung (Legal Collection)
       124=Energie (Energy)
       125=Öffentliche Statistik (Public Statistics)
       126=Soziale Sicherheit (Social Security)
    
    Return as JSON:
    {{
        "title": "API Title",
        "description": "Detailed description of the API with at least 150-200 words...",
        "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
        "theme_codes": ["XXX", "YYY"]
    }}
    """

    # Make the API call to OpenAI with optimized settings
    try:
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            max_tokens=1000,  # Increased to allow for longer descriptions
            temperature=0.7  # Slightly more creative but still focused
        )
        
        # Extract and parse the response content
        content = response.choices[0].message.content
        
        result = json.loads(content)
        
        return result
    except Exception as e:
        logger.error(f"OpenAI API error: {str(e)}")
        logger.error("OpenAI API call failed", exc_info=True)
        return {
            "error": "Failed to generate content. Please try again."
        }