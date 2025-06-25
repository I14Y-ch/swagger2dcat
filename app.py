import os
import io
import json
import uuid
import threading
import time
import tempfile
import pickle
import requests
import re
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file, flash
from dotenv import load_dotenv
from utils.openai_utils import generate_api_description
from utils.deepl_utils import translate_content

# Load environment variables
load_dotenv()

# Try to import config, if it exists
try:
    from config import OPENAI_API_KEY, DEEPL_API_KEY
except ImportError:
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    DEEPL_API_KEY = os.getenv('DEEPL_API_KEY')

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'swagger2dcat-secret-key')

# Create session directory
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'session_storage'), exist_ok=True)

# Global dictionary to store processing results (in production, you'd use Redis or similar)
processing_results = {}

# Global cache for agents to avoid repeated API calls
agents_cache = {
    'data': None,
    'timestamp': None,
    'cache_duration': 3600  # 1 hour
}

def get_cached_agents():
    """Get agents from cache or fetch if cache is expired"""
    current_time = time.time()
    
    # Check if cache is valid
    if (agents_cache['data'] is not None and 
        agents_cache['timestamp'] is not None and 
        current_time - agents_cache['timestamp'] < agents_cache['cache_duration']):
        return agents_cache['data']
    
    # Fetch fresh data
    try:
        from utils.i14y_utils import get_agents
        agents = get_agents()
        
        # Update cache
        agents_cache['data'] = agents
        agents_cache['timestamp'] = current_time
        
        return agents
    except Exception as e:
        print(f"Error fetching agents: {str(e)}")
        # Return cached data even if expired, or empty list
        return agents_cache['data'] if agents_cache['data'] is not None else []

def save_processing_data(processing_id, data):
    """Save processing data to temporary file to avoid session size limits"""
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(temp_dir, f"swagger2dcat_{processing_id}.pkl")
    
    try:
        with open(temp_file, 'wb') as f:
            pickle.dump(data, f)
        return True
    except Exception as e:
        print(f"Error saving processing data: {str(e)}")
        return False

def load_processing_data(processing_id):
    """Load processing data from temporary file"""
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(temp_dir, f"swagger2dcat_{processing_id}.pkl")
    
    try:
        if os.path.exists(temp_file):
            with open(temp_file, 'rb') as f:
                data = pickle.load(f)
            # Clean up the file after loading
            os.remove(temp_file)
            return data
    except Exception as e:
        print(f"Error loading processing data: {str(e)}")
    
    return None

def cleanup_old_temp_files():
    """Clean up old temporary files"""
    import glob
    
    temp_dir = tempfile.gettempdir()
    pattern = os.path.join(temp_dir, "swagger2dcat_*.pkl")
    current_time = time.time()
    
    for file_path in glob.glob(pattern):
        try:
            # Remove files older than 1 hour
            if os.path.getmtime(file_path) < current_time - 3600:
                os.remove(file_path)
        except Exception:
            pass  # Ignore errors during cleanup

# Global dictionary to store processing results (in production, you'd use Redis or similar)
processing_results = {}

def detect_office_id_from_url(url, agents):
    """
    Try to detect the office abbreviation from a .admin.ch URL and return the matching agency id (e.g., CH_BAFU)
    """
    if not url or "admin.ch" not in url:
        return None
    # Extract subdomain before .admin.ch
    match = re.search(r"https?://([a-z0-9\-]+)\.admin\.ch", url, re.IGNORECASE)
    if match:
        abbrev = match.group(1)
        if abbrev:
            # Try both upper and lower case
            possible_ids = [f"CH_{abbrev.upper()}", f"CH_{abbrev.lower()}"]
            for pid in possible_ids:
                if any(agent.get('id', '').lower() == pid.lower() for agent in agents):
                    return pid
    return None

@app.route('/')
def index():
    # Clear session only on a fresh start
    session.clear()
    return redirect(url_for('url'))

@app.route('/url', methods=['GET', 'POST'])
def url():
    if request.method == 'POST':
        # Get form data
        swagger_url = request.form.get('swagger_url', '')
        landing_page_url = request.form.get('landing_page_url', '')
        
        # Validate swagger URL
        if not swagger_url:
            return render_template('url.html', 
                                  swagger_url=swagger_url, 
                                  landing_page_url=landing_page_url,
                                  error='Please provide a valid Swagger/OpenAPI URL')
        
        # Clean up old temp files
        cleanup_old_temp_files()
        
        # Generate a unique processing ID
        processing_id = str(uuid.uuid4())
        
        # Store minimal data in session
        session['swagger_url'] = swagger_url
        session['landing_page_url'] = landing_page_url
        session['processing_id'] = processing_id
        session['processing_status'] = 'processing'
        
        # Initialize processing result with minimal data
        processing_results[processing_id] = {
            'status': 'processing',
            'error': None
        }
        
        # Start background processing
        def process_api_data(proc_id, swagger_url, landing_page_url):
            try:
                # Parse Swagger specification (now with URL detection)
                from utils.swagger_utils import extract_swagger_info
                swagger_info = extract_swagger_info(swagger_url)
                
                # Check if URL was auto-detected and log it
                if swagger_info.get('url_detected'):
                    pass  # Auto-detected JSON URL
                
                # Extract content from landing page if provided
                landing_page_content = ""
                document_links = []
                address_data = {}
                if landing_page_url:
                    from utils.web_utils import extract_web_content
                    # Now returns address_data as 5th value
                    web_title, web_description, web_content, doc_links, address_data = extract_web_content(landing_page_url)
                    
                    if web_content:
                        landing_page_content = web_description or web_content
                        document_links = doc_links
                
                # Get the list of agents (using cache)
                agents = get_cached_agents()
                agents_error = None if agents else "Failed to fetch agents"
                
                # Save processing data to temporary file
                processing_data = {
                    'swagger_info': swagger_info,
                    'landing_page_content': landing_page_content,
                    'document_links': document_links,
                    'agents': agents,
                    'agents_error': agents_error,
                    'address_data': address_data
                }
                
                if save_processing_data(proc_id, processing_data):
                    # Mark processing as complete
                    processing_results[proc_id]['status'] = 'complete'
                else:
                    processing_results[proc_id]['status'] = 'error'
                    processing_results[proc_id]['error'] = "Failed to save processing data"
                
            except Exception as e:
                processing_results[proc_id]['status'] = 'error'
                processing_results[proc_id]['error'] = str(e)
        
        # Start the background thread
        thread = threading.Thread(target=process_api_data, args=(processing_id, swagger_url, landing_page_url))
        thread.daemon = True
        thread.start()
        
        # Redirect to loading page
        return redirect(url_for('loading'))
    else:
        # Check if this is a direct visit vs. backward navigation
        # If coming from higher steps (backward navigation), keep session data
        from_step = request.args.get('from_step')
        error_message = request.args.get('error')
        
        # Only clear session if this is a direct visit (not backward navigation)
        if not from_step:
            session.clear()
        
        # Get the existing URL data if navigating backward
        swagger_url = session.get('swagger_url', '')
        landing_page_url = session.get('landing_page_url', '')
        
        return render_template('url.html', 
                             swagger_url=swagger_url,
                             landing_page_url=landing_page_url,
                             current_step=1,
                             error=error_message)

@app.route('/loading')
def loading():
    return render_template('loading.html', current_step=1)

@app.route('/check_processing_status')
def check_processing_status():
    processing_id = session.get('processing_id')
    
    if not processing_id:
        return jsonify({'status': 'error', 'message': 'No processing ID found in session'})
    
    if processing_id not in processing_results:
        return jsonify({'status': 'error', 'message': 'Processing ID not found'})
    
    result = processing_results[processing_id]
    status = result['status']
    
    if status == 'complete':
        # Load results from temporary file
        processing_data = load_processing_data(processing_id)
        
        if processing_data:
            # Store only essential data in session to avoid size limits
            session['swagger_info'] = processing_data['swagger_info']
            session['landing_page_content'] = processing_data['landing_page_content']
            session['document_links'] = processing_data.get('document_links', [])
            
            # Store agents count instead of full list initially
            session['agents_count'] = len(processing_data['agents']) if processing_data['agents'] else 0
            
            # Store agents in a separate temporary file for step2
            agents_temp_id = str(uuid.uuid4())
            session['agents_temp_id'] = agents_temp_id
            save_processing_data(agents_temp_id, {'agents': processing_data['agents']})
            
            if processing_data['agents_error']:
                session['agents_error'] = processing_data['agents_error']
        
        # Clean up
        if processing_id in processing_results:
            del processing_results[processing_id]
        session['processing_status'] = 'complete'
        
        # Store address_data in session for later use (step 4)
        session['address_data'] = processing_data.get('address_data', {})
        
        return jsonify({'status': 'complete'})
    elif status == 'error':
        error_message = result['error']
        session['processing_status'] = 'error'
        session['processing_error'] = error_message
        
        # Clean up
        if processing_id in processing_results:
            del processing_results[processing_id]
        
        return jsonify({'status': 'error', 'message': error_message})
    else:
        return jsonify({'status': 'processing'})

@app.route('/ai')
def ai():
    # Check if we have the necessary data in the session
    if 'swagger_url' not in session:
        flash("Please start from step 1.", "warning")
        return redirect(url_for('url'))
    
    # Check if processing is complete
    if session.get('processing_status') != 'complete':
        return redirect(url_for('loading'))
    
    # Get data from session
    swagger_url = session.get('swagger_url', '')
    landing_page_url = session.get('landing_page_url', '')
    
    # Get pre-processed swagger info
    swagger_info = session.get('swagger_info', {})

    # Check if there was an error parsing the Swagger
    if 'error' in swagger_info:
        error_message = swagger_info['error']
        flash(f"Note: {error_message} The form will be empty.", "warning")
        swagger_info = {
            'title': '',
            'description': '',
            'keywords': [],
            'additional_info': ''
        }
    
    # Check if URL was auto-detected and show info
    if swagger_info.get('url_detected'):
        flash(f"JSON URL auto-detected: {swagger_info['resolved_url']}", "info")
    
    # Get pre-processed landing page content
    landing_page_content = session.get('landing_page_content', '')
    document_links = session.get('document_links', [])

    # Prepare description with additional information
    full_description = swagger_info.get('description', '')
    additional_info = swagger_info.get('additional_info', '')
    version = swagger_info.get('version', '')
    endpoint_summary = swagger_info.get('endpoint_summary', '')
    endpoint_short_descriptions = swagger_info.get('endpoint_short_descriptions', [])

    # Add web content to description if available (up to 3000 chars)
    if landing_page_content:
        web_content_excerpt = landing_page_content[:3000]  # Use up to 3000 characters
        full_description += f"\n\n--- Additional information from {landing_page_url} ---\n\n{web_content_excerpt}"
        
        # Add document links if available
        if document_links:
            full_description += "\n\n--- Document Links ---\n"
            for doc in document_links[:10]:  # Limit to first 10 documents
                full_description += f"\n- {doc['label']}: {doc['href']}"
            if len(document_links) > 10:
                full_description += f"\n... and {len(document_links) - 10} more documents"

    if version:
        full_description += f"\n\nVersion: {version}"

    if additional_info:
        full_description += f"\n\n{additional_info}"

    # Add endpoint summary if available
    if endpoint_summary:
        full_description += f"\n\n--- Endpoint Summary ---\n\n{endpoint_summary}"

    # Add endpoint short descriptions if available
    if endpoint_short_descriptions:
        full_description += "\n\n--- Endpoint Details ---\n\n"
        for ep in endpoint_short_descriptions[:30]:
            full_description += f"{ep['method']} {ep['path']}: {ep['short_description']}\n"
        if len(endpoint_short_descriptions) > 30:
            full_description += f"... and {len(endpoint_short_descriptions) - 30} more endpoints\n"

    # Get any previously entered or generated content (prioritize existing content)
    title = session.get('generated_title', '') or swagger_info.get('title', '')
    description = session.get('generated_description', '') or full_description
    
    # Get keywords from Swagger or session
    keywords = session.get('generated_keywords', []) or swagger_info.get('keywords', [])
    if isinstance(keywords, list):
        keywords_display = ', '.join(keywords)
    else:
        keywords_display = keywords
    
    # Get theme codes - now directly from session as a list
    theme_codes = session.get('theme_codes', [])
    
    # Fallback to single theme_code if theme_codes is not set (backward compatibility)
    if not theme_codes:
        theme_code = session.get('theme_code', '')
        if theme_code:
            theme_codes = [theme_code]
    
    # Load agents from temporary storage
    agents = []
    agents_temp_id = session.get('agents_temp_id')
    if agents_temp_id:
        agents_data = load_processing_data(agents_temp_id)
        if agents_data and 'agents' in agents_data:
            agents = agents_data['agents']
    
    # Fallback to cached agents if temp data not available
    if not agents:
        agents = get_cached_agents()
    
    if not agents and 'agents_error' in session:
        flash("Failed to load publishers: " + session['agents_error'], "danger")
    
    # Get selected agency
    selected_agency = session.get('selected_agency', '')

    # --- Office detection logic ---
    if not selected_agency:
        detected_agency = None
        # Try swagger_url first, then landing_page_url
        for url in [session.get('swagger_url', ''), session.get('landing_page_url', '')]:
            detected_agency = detect_office_id_from_url(url, agents)
            if detected_agency:
                break
        if detected_agency:
            selected_agency = detected_agency
            session['selected_agency'] = selected_agency
    
    # Get access rights (default to PUBLIC)
    access_rights_code = session.get('access_rights_code', 'PUBLIC')
    
    # Access rights options
    access_rights_options = [
        {'code': 'PUBLIC', 'label': 'Public - Accessible to everyone'},
        {'code': 'RESTRICTED', 'label': 'Restricted - Limited access'},
        {'code': 'NON_PUBLIC', 'label': 'Non-public - Internal use only'},
        {'code': 'CONDITIONAL', 'label': 'Conditional - Access under certain conditions'}
    ]
    
    # Get license (default to empty)
    license_code = session.get('license_code', '')
    
    # License options - only valid I14Y license codes
    license_options = [
        {'code': '', 'label': 'No license specified'},
        {'code': 'terms_open', 'label': 'Opendata OPEN: Freie Nutzung (Open use)'},
        {'code': 'terms_by', 'label': 'Opendata BY: Freie Nutzung. Quellenangabe ist Pflicht (Open use. Must provide source)'},
        {'code': 'terms_ask', 'label': 'Opendata ASK: Kommerzielle Nutzung nur mit Bewilligung (Commercial use requires permission)'},
        {'code': 'terms_by_ask', 'label': 'Opendata BY ASK: Quellenangabe + kommerzielle Bewilligung erforderlich (Source + commercial permission required)'}
    ]
    
    # Display the step2 template with pre-filled content
    return render_template('ai.html', 
                          swagger_url=swagger_url,
                          landing_page_url=landing_page_url,
                          title=title,
                          description=description,
                          keywords=keywords_display,
                          theme_codes=theme_codes,
                          agents=agents,
                          selected_agency=selected_agency,
                          access_rights_code=access_rights_code,
                          access_rights_options=access_rights_options,
                          license_code=license_code,
                          license_options=license_options,
                          current_step=2)

@app.route('/generate', methods=['POST'])
def generate():
    swagger_url = session.get('swagger_url')
    landing_page_url = session.get('landing_page_url')
    landing_page_content = session.get('landing_page_content', '')

    if not swagger_url:
        return jsonify({"error": "No swagger URL provided. Please go back to step 1."})

    # Call OpenAI to generate the API description
    generated_content = generate_api_description(
        swagger_url=swagger_url,
        landing_page_url=landing_page_url,
        landing_page_content=landing_page_content
    )

    # Store the generated content in the session or return an error
    if "error" in generated_content:
        return jsonify(generated_content)

    session['generated_title'] = generated_content.get('title', '')
    session['generated_description'] = generated_content.get('description', '')
    session['generated_keywords'] = generated_content.get('keywords', [])
    
    # Handle multiple theme codes - store either as a list or convert single code to list
    theme_codes = generated_content.get('theme_codes', [])
    # For backward compatibility, also check for single theme_code
    if not theme_codes and 'theme_code' in generated_content:
        theme_codes = [generated_content['theme_code']]
    
    session['theme_codes'] = theme_codes

    return jsonify(generated_content)

@app.route('/translation')
def translation():
    # Check if we have the necessary data in the session
    if 'swagger_url' not in session:
        flash("Please start from step 1.", "warning")
        return redirect(url_for('url'))
    
    # Check if we have metadata (either generated or manually entered)
    if not session.get('generated_title') and not session.get('selected_agency'):
        flash("Please complete step 2 first.", "warning")
        return redirect(url_for('ai'))
    
    # Get content from session (from step 2)
    title = session.get('generated_title', '')
    description = session.get('generated_description', '')
    keywords = session.get('generated_keywords', [])
    
    # Format keywords as a string for display
    if isinstance(keywords, list):
        keywords_display = ', '.join(keywords)
    else:
        keywords_display = keywords
    
    # Import our session utilities
    from utils.session_utils import save_to_session_file, load_from_session_file
    
    # Get any existing translations
    translations = load_from_session_file('translations', {})
    # If not in file, try session for backward compatibility
    if not translations:
        translations = session.get('translations', {})
    
    # Initialize translations structure if it doesn't exist
    if not translations:
        # Create a basic translations structure with the English content
        translations = {
            'en': {
                'title': title,
                'description': description,
                'keywords': keywords if isinstance(keywords, list) else []
            },
            'de': {'title': '', 'description': '', 'keywords': []},
            'fr': {'title': '', 'description': '', 'keywords': []},
            'it': {'title': '', 'description': '', 'keywords': []}
        }
        # Import our session utilities
        from utils.session_utils import save_to_session_file
        # Store in file instead of session to avoid size limits
        save_to_session_file('translations', translations)
        session['translations_available'] = True
    
    # German translations
    title_de = ''
    description_de = ''
    keywords_de = ''
    
    # French translations
    title_fr = ''
    description_fr = ''
    keywords_fr = ''
    
    # Italian translations
    title_it = ''
    description_it = ''
    keywords_it = ''
    
    # If we have translations, extract them
    if translations:
        # German
        if 'de' in translations:
            title_de = translations['de'].get('title', '')
            description_de = translations['de'].get('description', '')
            keywords_de = ', '.join(translations['de'].get('keywords', []))
        
        # French
        if 'fr' in translations:
            title_fr = translations['fr'].get('title', '')
            description_fr = translations['fr'].get('description', '')
            keywords_fr = ', '.join(translations['fr'].get('keywords', []))
        
        # Italian
        if 'it' in translations:
            title_it = translations['it'].get('title', '')
            description_it = translations['it'].get('description', '')
            keywords_it = ', '.join(translations['it'].get('keywords', []))
    
    # Get contact point info from session (created in save_api_details)
    contact_point = session.get('contact_point', {
        "emailInternet": "",
        "telWorkVoice": "",
        "org": {"de": "", "en": "", "fr": "", "it": ""},
        "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
        "note": {"de": "", "en": "", "fr": "", "it": ""}
    })
    
    # Display the translation template with contact point info
    return render_template('translation.html',
                          title=title,
                          description=description,
                          keywords=keywords_display,
                          title_de=title_de,
                          description_de=description_de,
                          keywords_de=keywords_de,
                          title_fr=title_fr,
                          description_fr=description_fr,
                          keywords_fr=keywords_fr,
                          title_it=title_it,
                          description_it=description_it,
                          keywords_it=keywords_it,
                          contact_point=contact_point,
                          current_step=3)

@app.route('/upload', methods=['GET'])
def upload():
    # Check if we have the basic data in the session
    if 'swagger_url' not in session:
        flash("Please start from step 1.", "warning")
        return redirect(url_for('url'))
    
    # Get agents for publisher name resolution
    agents = get_cached_agents()
    theme_codes = session.get('theme_codes', [])
    selected_agency = session.get('selected_agency', '')
    access_rights_code = session.get('access_rights_code', 'PUBLIC')
    license_code = session.get('license_code', '')
    swagger_url = session.get('swagger_url', '')
    landing_page_url = session.get('landing_page_url', '')
    document_links = session.get('document_links', [])
    
    # Import our session utilities
    from utils.session_utils import save_to_session_file, load_from_session_file
    
    # Get translations - first try to load from file
    translations = load_from_session_file('translations', {})
    
    # If not in file, check session (backwards compatibility)
    if not translations:
        translations = session.get('translations', {})
        
    if not translations and session.get('generated_title'):
        # Create from generated content if available
        title = session.get('generated_title', '')
        description = session.get('generated_description', '')
        keywords = session.get('generated_keywords', [])
        
        translations = {
            'en': {
                'title': title,
                'description': description,
                'keywords': keywords if isinstance(keywords, list) else []
            },
            'de': {'title': '', 'description': '', 'keywords': []},
            'fr': {'title': '', 'description': '', 'keywords': []},
            'it': {'title': '', 'description': '', 'keywords': []}
        }
        # Save to file instead of session
        save_to_session_file('translations', translations)
        session['translations_available'] = True
    elif not translations:
        flash("Please complete the translation step first.", "warning")
        return redirect(url_for('translation'))
    
    # Initialize default contact point structure if missing
    default_contact_point = {
        "emailInternet": "",
        "org": {"de": "", "en": "", "fr": "", "it": ""},
        "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
        "note": {"de": "", "en": "", "fr": "", "it": ""},
        "telWorkVoice": "",
        "fn": {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
    }

    # Load address_data from session if available (set in check_processing_status)
    address_data = session.get('address_data', {})

    # Allow editing of all fields
    if request.method == 'POST':
        # Get contact point from session or use default
        contact_point = session.get('contact_point', default_contact_point)
        # Ensure fn field exists and has all required languages
        if 'fn' not in contact_point or not isinstance(contact_point['fn'], dict):
            contact_point['fn'] = {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
        for lang in ["de", "en", "fr", "it", "rm"]:
            if lang not in contact_point['fn']:
                contact_point['fn'][lang] = ""

        # Save all reviewed content from the form
        translations['en']['title'] = request.form.get('title_en', '')
        translations['en']['description'] = request.form.get('description_en', '')
        translations['en']['keywords'] = [kw.strip() for kw in request.form.get('keywords_en', '').split(',') if kw.strip()]
        translations['de']['title'] = request.form.get('title_de', '')
        translations['de']['description'] = request.form.get('description_de', '')
        translations['de']['keywords'] = [kw.strip() for kw in request.form.get('keywords_de', '').split(',') if kw.strip()]
        translations['fr']['title'] = request.form.get('title_fr', '')
        translations['fr']['description'] = request.form.get('description_fr', '')
        translations['fr']['keywords'] = [kw.strip() for kw in request.form.get('keywords_fr', '').split(',') if kw.strip()]
        translations['it']['title'] = request.form.get('title_it', '')
        translations['it']['description'] = request.form.get('description_it', '')
        translations['it']['keywords'] = [kw.strip() for kw in request.form.get('keywords_it', '').split(',') if kw.strip()]
        
        # Contact point fields - add fn field for all languages
        contact_point['fn']['de'] = ""  # You can later allow editing, for now just empty string
        contact_point['fn']['en'] = ""
        contact_point['fn']['fr'] = ""
        contact_point['fn']['it'] = ""
        contact_point['fn']['rm'] = ""

        contact_point['org']['de'] = request.form.get('org_de', '')
        contact_point['org']['en'] = request.form.get('org_en', '')
        contact_point['org']['fr'] = request.form.get('org_fr', '')
        contact_point['org']['it'] = request.form.get('org_it', '')
        contact_point['adrWork']['de'] = request.form.get('adr_de', '')
        contact_point['adrWork']['en'] = request.form.get('adr_en', '')
        contact_point['adrWork']['fr'] = request.form.get('adr_fr', '')
        contact_point['adrWork']['it'] = request.form.get('adr_it', '')
        contact_point['emailInternet'] = request.form.get('emailInternet', '')
        contact_point['telWorkVoice'] = request.form.get('telWorkVoice', '')
        contact_point['note']['de'] = request.form.get('note_de', '')
        contact_point['note']['en'] = request.form.get('note_en', '')
        contact_point['note']['fr'] = request.form.get('note_fr', '')
        contact_point['note']['it'] = request.form.get('note_it', '')

        # Process document links
        doc_labels = request.form.getlist('doc_label[]')
        doc_hrefs = request.form.getlist('doc_href[]')
        
        # Rebuild document links
        document_links = []
        for i in range(len(doc_labels)):
            if i < len(doc_hrefs) and doc_hrefs[i].strip():
                href = doc_hrefs[i].strip()
                label = doc_labels[i].strip() if i < len(doc_labels) else href.split('/')[-1]
                doc_type = href.split('.')[-1].lower() if '.' in href else ''
                
                document_links.append({
                    'href': href,
                    'label': label,
                    'type': doc_type
                })
        
        # Save back to session
        session['document_links'] = document_links

        # Save translations to file to avoid session size limits
        save_to_session_file('translations', translations)
        session['translations_available'] = True
        
        # Save contact point - this is small enough for the session
        session['contact_point'] = contact_point
        flash("Review changes saved. You can now submit or download the JSON.", "success")
        # Re-render the page with updated data
    else:
        contact_point = session.get('contact_point', default_contact_point)
        # Ensure fn field exists and has all required languages
        if 'fn' not in contact_point or not isinstance(contact_point['fn'], dict):
            contact_point['fn'] = {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
        for lang in ["de", "en", "fr", "it", "rm"]:
            if lang not in contact_point['fn']:
                contact_point['fn'][lang] = ""

        # --- Prefill contact point fields from address_data if available and fields are empty ---
        if address_data:
            # Prefill email
            if not contact_point.get('emailInternet'):
                contact_point['emailInternet'] = address_data.get('email', '')
            # Prefill phone
            if not contact_point.get('telWorkVoice'):
                contact_point['telWorkVoice'] = address_data.get('phone', '')
            # Prefill org (all languages)
            org_name = address_data.get('organization', '')
            if org_name:
                for lang in ['de', 'en', 'fr', 'it']:
                    if not contact_point['org'].get(lang):
                        contact_point['org'][lang] = org_name
            # Prefill address (all languages)
            adr = address_data.get('address', '')
            if adr:
                for lang in ['de', 'en', 'fr', 'it']:
                    if not contact_point['adrWork'].get(lang):
                        contact_point['adrWork'][lang] = adr
            # Prefill note (all languages)
            note = address_data.get('note', '')
            if note:
                for lang in ['de', 'en', 'fr', 'it']:
                    if not contact_point['note'].get(lang):
                        contact_point['note'][lang] = note

    # Print debug info to console
    print(f"DEBUG - translations: {translations}")
    print(f"DEBUG - translations type: {type(translations)}")
    print(f"DEBUG - contact_point: {session.get('contact_point')}")

    # Generate the JSON preview
    from utils.json_utils import generate_dcat_json
    json_data = generate_dcat_json(
        translations=translations,
        theme_codes=theme_codes,
        agency_id=selected_agency,
        swagger_url=swagger_url,
        landing_page_url=landing_page_url,
        agents_list=agents,
        access_rights_code=access_rights_code,
        license_code=license_code,
        contact_point_override=contact_point,
        document_links=document_links
    )
    json_preview = json.dumps(json_data, indent=2)

    # Render the template with editable fields
    return render_template('upload.html',
        translations=translations,
        contact_point=contact_point,
        theme_codes=theme_codes,
        selected_agency=selected_agency,
        access_rights_code=access_rights_code,
        license_code=license_code,
        swagger_url=swagger_url,
        landing_page_url=landing_page_url,
        document_links=document_links,
        json_preview=json_preview,
        current_step=4
    )

@app.route('/translate', methods=['POST'])
def translate():
    # Import our session utilities
    from utils.session_utils import save_to_session_file, load_from_session_file
    
    # Get the content from the form
    title_en = request.form.get('title_en', '')
    description_en = request.form.get('description_en', '')
    keywords_en = request.form.get('keywords_en', '')
    title_de = request.form.get('title_de', '')
    description_de = request.form.get('description_de', '')
    keywords_de = request.form.get('keywords_de', '')
    
    # Validate required fields
    if not title_en or not description_en:
        flash("English title and description are required.", "danger")
        return redirect(url_for('translation'))
    
    # Save to session
    session['title_en'] = title_en
    session['description_en'] = description_en
    session['keywords_en'] = keywords_en
    session['title_de'] = title_de
    session['description_de'] = description_de
    session['keywords_de'] = keywords_de
    
    # Process keywords - convert from comma-separated string to list
    keywords_en_list = [kw.strip() for kw in keywords_en.split(',') if kw.strip()]
    keywords_de_list = [kw.strip() for kw in keywords_de.split(',') if kw.strip()]
    
    try:
        # Import translation functions
        from utils.deepl_utils import translate_content, translate_from_english
        
        # First check if we have German content provided by user
        if title_de and description_de and keywords_de:
            # We have both English and German, so use both for translations
            translations = translate_content(
                title_de=title_de,
                description_de=description_de,
                keywords_de=keywords_de_list,
                title_en=title_en,
                description_en=description_en,
                keywords_en=keywords_en_list
            )
        else:
            # We only have English content, translate from English to all languages
            translations = translate_from_english(
                title_en=title_en,
                description_en=description_en,
                keywords_en=keywords_en_list
            )
        
        # Instead of storing in session, save to file to avoid session size limitations
        save_to_session_file('translations', translations)
        # Keep a minimal reference in the cookie session
        session['translations_available'] = True
        
        # Also preserve the contact point information if it exists
        if 'contact_point' not in session:
            # Initialize with default values
            session['contact_point'] = {
                "emailInternet": "",
                "org": {"de": "", "en": "", "fr": "", "it": ""},
                "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
                "note": {"de": "", "en": "", "fr": "", "it": ""},
                "telWorkVoice": ""
            }
        
        flash("Translation completed successfully!", "success")
        
        # Redirect to final step
        return redirect(url_for('upload'))
    except Exception as e:
        flash(f"Translation failed: {str(e)}", "danger")
        return redirect(url_for('translation'))

@app.route('/save_api_details', methods=['POST'])
def save_api_details():
    # Get all form data
    title = request.form.get('title', '')
    description = request.form.get('description', '')
    keywords = request.form.get('keywords', '')
    agency = request.form.get('agency', '')
    access_rights_code = request.form.get('access_rights_code', 'PUBLIC')
    license_code = request.form.get('license_code', '')
    
    # Get selected theme codes (multiple selection)
    theme_codes = request.form.getlist('theme_codes')
    
    # Process keywords - convert from comma-separated string to list
    keywords_list = [kw.strip() for kw in keywords.split(',') if kw.strip()]
    
    # Validate required fields
    if not title:
        flash("Title is required.", "danger")
        return redirect(url_for('ai'))
        
    if not description:
        flash("Description is required.", "danger")
        return redirect(url_for('ai'))
        
    if not agency:
        flash("Publisher is required.", "danger")
        return redirect(url_for('ai'))
    
    # Save to session
    session['generated_title'] = title
    session['generated_description'] = description
    session['generated_keywords'] = keywords_list
    session['theme_codes'] = theme_codes
    session['selected_agency'] = agency
    session['access_rights_code'] = access_rights_code
    session['license_code'] = license_code
    
    # Create initial translations structure
    session['translations'] = {
        'en': {
            'title': title,
            'description': description,
            'keywords': keywords_list
        },
        'de': {'title': '', 'description': '', 'keywords': []},
        'fr': {'title': '', 'description': '', 'keywords': []},
        'it': {'title': '', 'description': '', 'keywords': []}
    }
    
    # Get agents for publisher name resolution and prefill contact point
    agents = get_cached_agents()
    
    # Initialize default contact point
    contact_point = {
        "emailInternet": "",
        "telWorkVoice": "",
        "org": {"de": "", "en": "", "fr": "", "it": ""},
        "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
        "note": {"de": "", "en": "", "fr": "", "it": ""}
    }
    
    # Try to prefill from agency data
    if agency and agents:
        # Use dictionary access (not attribute access) since agents are dictionaries
        selected_agent = next((a for a in agents if a.get('id') == agency), None)
        if selected_agent:
            # Get the organization name (multilingual if available)
            if 'name' in selected_agent:
                if isinstance(selected_agent['name'], dict):
                    # Handle multilingual name
                    for lang in ['de', 'en', 'fr', 'it']:
                        if lang in selected_agent['name'] and selected_agent['name'][lang]:
                            contact_point['org'][lang] = selected_agent['name'][lang]
                else:
                    # Use display_name as fallback for all languages
                    display_name = selected_agent.get('display_name', 'Unknown Organization')
                    for lang in ['de', 'en', 'fr', 'it']:
                        contact_point['org'][lang] = display_name
            
            # Get address information if available
            address_info = selected_agent.get('address', {})
            if address_info and isinstance(address_info, dict):
                # Extract email and phone if available
                contact_point['emailInternet'] = address_info.get('email', '')
                contact_point['telWorkVoice'] = address_info.get('phone', '')
                
                # Try to build a meaningful address string
                parts = []
                
                # Add organization info if available
                if 'organization' in address_info and isinstance(address_info['organization'], dict):
                    org_name = address_info['organization'].get('name', {})
                    if isinstance(org_name, dict):
                        org_name_str = org_name.get('de') or org_name.get('en') or next(iter(org_name.values()), '')
                        if org_name_str:
                            parts.append(org_name_str)
                
                # Add department info if available
                if 'department' in address_info and isinstance(address_info['department'], dict):
                    dept_name = address_info['department'].get('name', {})
                    if isinstance(dept_name, dict):
                        dept_name_str = dept_name.get('de') or dept_name.get('en') or next(iter(dept_name.values()), '')
                        if dept_name_str:
                            parts.append(dept_name_str)
                
                address_str = ", ".join(parts)
                
                # Use the same address string for all languages for now
                for lang in ['de', 'en', 'fr', 'it']:
                    contact_point['adrWork'][lang] = address_str
    
    # Save contact point to session
    session['contact_point'] = contact_point
    
    # Redirect to step 3 (translation page)
    return redirect(url_for('translation'))

@app.route('/download_json', methods=['GET', 'POST'])
def download_json():
    # Check if we have the necessary data
    if 'translations' not in session:
        return redirect(url_for('translation'))

    # Get required data from session
    translations = session.get('translations', {})
    theme_codes = session.get('theme_codes', [])
    selected_agency = session.get('selected_agency', '')
    access_rights_code = session.get('access_rights_code', 'PUBLIC')
    license_code = session.get('license_code', '')

    # Get agents for publisher name resolution
    agents = get_cached_agents()

    # Generate the JSON
    from utils.json_utils import generate_dcat_json

    # Get contact point from session or use default, ensure fn is present
    default_contact_point = {
        "emailInternet": "",
        "org": {"de": "", "en": "", "fr": "", "it": ""},
        "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
        "note": {"de": "", "en": "", "fr": "", "it": ""},
        "telWorkVoice": "",
        "fn": {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
    }
    contact_point = session.get('contact_point', default_contact_point)
    if 'fn' not in contact_point or not isinstance(contact_point['fn'], dict):
        contact_point['fn'] = {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
    for lang in ["de", "en", "fr", "it", "rm"]:
        if lang not in contact_point['fn']:
            contact_point['fn'][lang] = ""

    json_data = generate_dcat_json(
        translations=translations,
        theme_codes=theme_codes,
        agency_id=selected_agency,
        swagger_url=session.get('swagger_url', ''),
        landing_page_url=session.get('landing_page_url', ''),
        agents_list=agents,
        access_rights_code=access_rights_code,
        license_code=license_code,
        contact_point_override=contact_point
    )

    # Convert to pretty JSON string
    import json
    json_string = json.dumps(json_data, indent=2)

    # Create a response with the JSON data
    import io
    from flask import send_file

    # Generate filename based on the API title
    api_title = translations.get('en', {}).get('title', 'api')
    filename = f"{api_title.lower().replace(' ', '_')}_dcat.json"

    # Create in-memory file
    mem = io.BytesIO()
    mem.write(json_string.encode('utf-8'))
    mem.seek(0)

    # Send the file as an attachment
    return send_file(
        mem,
        mimetype='application/json',
        as_attachment=True,
        download_name=filename
    )

@app.route('/save_reviewed_content', methods=['POST'])
def save_reviewed_content():
    # Get the reviewed content from the form
    title_en = request.form.get('title_en', '')
    description_en = request.form.get('description_en', '')
    keywords_en = request.form.get('keywords_en', '')
    title_de = request.form.get('title_de', '')
    description_de = request.form.get('description_de', '')
    keywords_de = request.form.get('keywords_de', '')
    title_fr = request.form.get('title_fr', '')
    description_fr = request.form.get('description_fr', '')
    keywords_fr = request.form.get('keywords_fr', '')
    title_it = request.form.get('title_it', '')
    description_it = request.form.get('description_it', '')
    keywords_it = request.form.get('keywords_it', '')

    # Process keywords - convert from comma-separated string to list
    keywords_en_list = [kw.strip() for kw in keywords_en.split(',') if kw.strip()]
    keywords_de_list = [kw.strip() for kw in keywords_de.split(',') if kw.strip()]
    keywords_fr_list = [kw.strip() for kw in keywords_fr.split(',') if kw.strip()]
    keywords_it_list = [kw.strip() for kw in keywords_it.split(',') if kw.strip()]

    # Save the reviewed content to the session
    session['translations'] = {
        'en': {
            'title': title_en,
            'description': description_en,
            'keywords': keywords_en_list
        },
        'de': {
            'title': title_de,
            'description': description_de,
            'keywords': keywords_de_list
        },
        'fr': {
            'title': title_fr,
            'description': description_fr,
            'keywords': keywords_fr_list
        },
        'it': {
            'title': title_it,
            'description': description_it,
            'keywords': keywords_it_list
        }
    }

    # Redirect to the final step (JSON preview)
    return redirect(url_for('upload'))

@app.route('/submit_to_i14y', methods=['POST'])
def submit_to_i14y():
    """
    Submit the generated JSON data directly to the I14Y API
    """
    try:
        # Check if we have the necessary data
        if 'translations' not in session:
            return jsonify({
                'success': False, 
                'error': 'No translation data found. Please complete the previous steps.'
            })

        # Get the token from request
        request_data = request.get_json()
        if not request_data or 'token' not in request_data:
            return jsonify({
                'success': False, 
                'error': 'No access token provided.'
            })
        
        token = request_data['token'].strip()
        
        # Validate token format
        if not token.lower().startswith('bearer '):
            return jsonify({
                'success': False, 
                'error': 'Invalid token format. Token must start with "Bearer ".'
            })

        # Get required data from session
        translations = session.get('translations', {})
        theme_codes = session.get('theme_codes', [])
        selected_agency = session.get('selected_agency', '')
        swagger_url = session.get('swagger_url', '')
        landing_page_url = session.get('landing_page_url', '')
        access_rights_code = session.get('access_rights_code', 'PUBLIC')
        license_code = session.get('license_code', '')

        # Validate required data
        if not translations:
            return jsonify({
                'success': False, 
                'error': 'No translations found. Please complete step 3.'
            })
        
        if not selected_agency:
            return jsonify({
                'success': False, 
                'error': 'No publisher selected. Please complete step 2.'
            })

        # Get agents for publisher name resolution
        agents = get_cached_agents()

        # Get contact point from session or use default, ensure fn is present
        default_contact_point = {
            "emailInternet": "",
            "org": {"de": "", "en": "", "fr": "", "it": ""},
            "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
            "note": {"de": "", "en": "", "fr": "", "it": ""},
            "telWorkVoice": "",
            "fn": {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
        }
        contact_point = session.get('contact_point', default_contact_point)
        if 'fn' not in contact_point or not isinstance(contact_point['fn'], dict):
            contact_point['fn'] = {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
        for lang in ["de", "en", "fr", "it", "rm"]:
            if lang not in contact_point['fn']:
                contact_point['fn'][lang] = ""

        # Generate the JSON data for I14Y
        from utils.json_utils import generate_dcat_json
        json_data = generate_dcat_json(
            translations=translations,
            theme_codes=theme_codes,
            agency_id=selected_agency,
            swagger_url=swagger_url,
            landing_page_url=landing_page_url,
            agents_list=agents,
            access_rights_code=access_rights_code,
            license_code=license_code,
            contact_point_override=contact_point  # <-- ensure contact_point_override is passed
        )

        # Submit to I14Y API
        i14y_response = submit_data_to_i14y_api(json_data, token)
        
        if i14y_response['success']:
            return jsonify({
                'success': True,
                'message': 'Successfully submitted to I14Y API',
                'dataset_id': i14y_response.get('dataset_id')
            })
        else:
            return jsonify({
                'success': False,
                'error': i14y_response['error']
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        })

def submit_data_to_i14y_api(json_data, token):
    """
    Submit data to the I14Y API endpoint
    
    Args:
        json_data (dict): The DCAT JSON data to submit
        token (str): The Bearer token for authentication
    
    Returns:
        dict: Response with success status and details
    """
    try:
        # I14Y API endpoint
        api_endpoint = "https://input.i14y.admin.ch/api/DataServiceInput"
        
        # Prepare headers
        headers = {
            'Authorization': token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # Make the API request with timeout
        response = requests.post(
            api_endpoint,
            json=json_data,
            headers=headers,
            timeout=30  # 30 second timeout
        )
        
        # Check if request was successful
        if response.status_code == 200 or response.status_code == 201:
            try:
                response_data = response.json()
                dataset_id = response_data.get('id') or response_data.get('datasetId') or 'Generated'
                return {
                    'success': True,
                    'dataset_id': dataset_id,
                    'response': response_data
                }
            except json.JSONDecodeError:
                # Some APIs return non-JSON success responses
                return {
                    'success': True,
                    'dataset_id': 'Submitted successfully',
                    'response': response.text
                }
        
        # Handle different error status codes
        elif response.status_code == 401:
            return {
                'success': False,
                'error': 'Authentication failed. Please check your access token.'
            }
        elif response.status_code == 403:
            return {
                'success': False,
                'error': 'Access forbidden. You may not have permission to submit data.'
            }
        elif response.status_code == 400:
            try:
                error_data = response.json()
                error_msg = error_data.get('message') or error_data.get('error') or 'Bad request'
                return {
                    'success': False,
                    'error': f'API error: {error_msg}'
                }
            except json.JSONDecodeError:
                return {
                    'success': False,
                    'error': f'API error (HTTP {response.status_code}): {response.text}'
                }
        elif response.status_code == 422:
            try:
                error_data = response.json()
                error_msg = error_data.get('message') or error_data.get('error') or 'Validation failed'
                return {
                    'success': False,
                    'error': f'Data validation failed: {error_msg}'
                }
            except json.JSONDecodeError:
                return {
                    'success': False,
                    'error': f'Data validation failed (422): {response.text}'
                }
        else:
            try:
                error_data = response.json()
                error_msg = error_data.get('message') or error_data.get('error') or f'HTTP {response.status_code}'
                return {
                    'success': False,
                    'error': f'API error: {error_msg}'
                }
            except json.JSONDecodeError:
                return {
                    'success': False,
                    'error': f'API error (HTTP {response.status_code}): {response.text}'
                }
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'error': 'Request timed out. The I14Y API may be temporarily unavailable.'
        }
    except requests.exceptions.ConnectionError:
        return {
            'success': False,
            'error': 'Connection error. Please check your internet connection and try again.'
        }
    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': f'Network error: {str(e)}'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }

@app.route('/autosave_review', methods=['POST'])
def autosave_review():
    """
    Autosave reviewed content from the upload (step 4) form via AJAX.
    """
    # Get translations from session or initialize
    from utils.session_utils import save_to_session_file, load_from_session_file

    translations = load_from_session_file('translations', {}) or session.get('translations', {})
    default_contact_point = {
        "emailInternet": "",
        "org": {"de": "", "en": "", "fr": "", "it": ""},
        "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
        "note": {"de": "", "en": "", "fr": "", "it": ""},
        "telWorkVoice": "",
        "fn": {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
    }
    contact_point = session.get('contact_point', default_contact_point)

    # Update translations
    translations['en']['title'] = request.form.get('title_en', '')
    translations['en']['description'] = request.form.get('description_en', '')
    translations['en']['keywords'] = [kw.strip() for kw in request.form.get('keywords_en', '').split(',') if kw.strip()]
    translations['de']['title'] = request.form.get('title_de', '')
    translations['de']['description'] = request.form.get('description_de', '')
    translations['de']['keywords'] = [kw.strip() for kw in request.form.get('keywords_de', '').split(',') if kw.strip()]
    translations['fr']['title'] = request.form.get('title_fr', '')
    translations['fr']['description'] = request.form.get('description_fr', '')
    translations['fr']['keywords'] = [kw.strip() for kw in request.form.get('keywords_fr', '').split(',') if kw.strip()]
    translations['it']['title'] = request.form.get('title_it', '')
    translations['it']['description'] = request.form.get('description_it', '')
    translations['it']['keywords'] = [kw.strip() for kw in request.form.get('keywords_it', '').split(',') if kw.strip()]

    # Update contact point
    contact_point['org']['de'] = request.form.get('org_de', '')
    contact_point['org']['en'] = request.form.get('org_en', '')
    contact_point['org']['fr'] = request.form.get('org_fr', '')
    contact_point['org']['it'] = request.form.get('org_it', '')
    contact_point['adrWork']['de'] = request.form.get('adr_de', '')
    contact_point['adrWork']['en'] = request.form.get('adr_en', '')
    contact_point['adrWork']['fr'] = request.form.get('adr_fr', '')
    contact_point['adrWork']['it'] = request.form.get('adr_it', '')
    contact_point['emailInternet'] = request.form.get('emailInternet', '')
    contact_point['telWorkVoice'] = request.form.get('telWorkVoice', '')
    contact_point['note']['de'] = request.form.get('note_de', '')
    contact_point['note']['en'] = request.form.get('note_en', '')
    contact_point['note']['fr'] = request.form.get('note_fr', '')
    contact_point['note']['it'] = request.form.get('note_it', '')
    if 'fn' not in contact_point or not isinstance(contact_point['fn'], dict):
        contact_point['fn'] = {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
    for lang in ["de", "en", "fr", "it", "rm"]:
        if lang not in contact_point['fn']:
            contact_point['fn'][lang] = ""

    # Update document links
    doc_labels = request.form.getlist('doc_label[]')
    doc_hrefs = request.form.getlist('doc_href[]')
    document_links = []
    for i in range(len(doc_labels)):
        if i < len(doc_hrefs) and doc_hrefs[i].strip():
            href = doc_hrefs[i].strip()
            label = doc_labels[i].strip() if i < len(doc_labels) else href.split('/')[-1]
            doc_type = href.split('.')[-1].lower() if '.' in href else ''
            document_links.append({
                'href': href,
                'label': label,
                'type': doc_type
            })
    session['document_links'] = document_links

    # Save to session and file
    session['translations'] = translations
    save_to_session_file('translations', translations)
    session['contact_point'] = contact_point

    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')