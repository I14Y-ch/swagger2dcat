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
from werkzeug.middleware.proxy_fix import ProxyFix
import glob

# Setup environment first before any other imports
from utils.env_setup import setup_environment
logger = setup_environment()

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file, flash
from flask_session import Session  # <-- Add this import
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Try to import config, if it exists
try:
    from config import OPENAI_API_KEY, DEEPL_API_KEY
except ImportError:
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    DEEPL_API_KEY = os.getenv('DEEPL_API_KEY')

# Log key presence (not the actual values)
logger.info(f"OPENAI_API_KEY available: {bool(OPENAI_API_KEY)}")
logger.info(f"DEEPL_API_KEY available: {bool(DEEPL_API_KEY)}")

# Create Flask app 
app = Flask(__name__, 
            static_url_path='/static',  # <-- Fixed: static files at /static
            template_folder='templates'
           )

# --- Session cleanup utility ---
def cleanup_old_sessions(session_dir, max_age_seconds=7200):
    """
    Delete session files older than max_age_seconds from the session directory.
    """
    now = time.time()
    session_files = glob.glob(os.path.join(session_dir, '*'))
    deleted = 0
    for f in session_files:
        try:
            if os.path.isfile(f):
                mtime = os.path.getmtime(f)
                if now - mtime > max_age_seconds:
                    os.remove(f)
                    deleted += 1
        except Exception as e:
            logger.warning(f"Could not delete session file {f}: {e}")
    if deleted > 0:
        logger.info(f"Deleted {deleted} expired session files from {session_dir}")

# Configure server-side session storage
session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'session_storage')
os.makedirs(session_dir, exist_ok=True)

# --- Clean up old sessions before initializing session interface ---
cleanup_old_sessions(session_dir, max_age_seconds=7200)

# Ensure permissions are set correctly for Docker environment
try:
    os.chmod(session_dir, 0o777)  # Make writable by all users (needed for Docker; WARNING: 0o777 makes the directory world-writable. Only use this in isolated Docker containers, never on shared or production hosts, as it poses a security risk.)
    if os.environ.get('DOCKERIZED', '').lower() == 'true':
        os.chmod(session_dir, 0o777)  # Relaxed permissions for Docker
    else:
        os.chmod(session_dir, 0o770)  # Restrict to owner/group
except Exception as e:
    logger.warning(f"Could not set permissions on session directory: {e}")

app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = session_dir
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_COOKIE_PATH'] = '/'  # <-- Fixed: cookie should work for root path
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Only use SECURE cookies if HTTPS is enabled
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('HTTPS_ENABLED', '').lower() == 'true'
Session(app)

# Apply ProxyFix for correct proxy handling (important for Digital Ocean)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

# Note: APPLICATION_ROOT removed - app runs at root path

# Initialize Flask app
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    logger.warning("SECRET_KEY not set in environment! Using default (insecure) key.")
    secret_key = 'swagger2dcat-secret-key'
app.secret_key = secret_key

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
        logger.error(f"Error fetching agents: {str(e)}")
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
        logger.error(f"Error saving processing data: {str(e)}")
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
        logger.error(f"Error loading processing data: {str(e)}")
    
    return None

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

# Then modify routes to use workflow_id parameter instead of session
@app.route('/')
def index():
    # Clear session only on a fresh start
    session.clear()
    return redirect(url_for('url'))

@app.route('/url', methods=['GET', 'POST'])
def url():
    if request.method == 'POST':
        # Create new workflow
        workflow_id = str(uuid.uuid4())
        
        # Initialize processing results entry
        processing_results[workflow_id] = {
            'status': 'processing',
            'created_at': time.time()
        }
        
        # Start processing in background
        def process_api_data(proc_id, swagger_url, landing_page_url):
            logger.info(f"Starting background processing for {proc_id}")
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
                    'address_data': address_data,
                    'swagger_url': swagger_url,
                    'landing_page_url': landing_page_url
                }
                
                if save_processing_data(proc_id, processing_data):
                    # Mark processing as complete
                    if proc_id in processing_results:
                        processing_results[proc_id]['status'] = 'complete'
                        logger.info(f"Processing completed for {proc_id}")
                else:
                    if proc_id in processing_results:
                        processing_results[proc_id]['status'] = 'error'
                        processing_results[proc_id]['error'] = "Failed to save processing data"
                        logger.error(f"Failed to save processing data for {proc_id}")
                
            except Exception as e:
                logger.error(f"Exception in background processing for {proc_id}: {str(e)}")
                if proc_id in processing_results:
                    processing_results[proc_id]['status'] = 'error'
                    processing_results[proc_id]['error'] = str(e)
                else:
                    # Create entry if it doesn't exist
                    processing_results[proc_id] = {
                        'status': 'error',
                        'error': str(e)
                    }
        
        # Start the background thread
        thread = threading.Thread(target=process_api_data, args=(workflow_id, request.form.get('swagger_url', ''), request.form.get('landing_page_url', '')))
        thread.daemon = True
        thread.start()
        
        # Redirect to loading page
        return redirect(url_for('loading', workflow_id=workflow_id))
    else:
        # Start new workflow
        return render_template('url.html')

@app.route('/loading')
def loading():
    workflow_id = request.args.get('workflow_id')
    if not workflow_id:
        return redirect(url_for('url'))
    
    return render_template('loading.html', 
                          workflow_id=workflow_id)

@app.route('/check_processing_status')
def check_processing_status():
    """
    Simplified processing status check that works more reliably
    """
    # Get processing ID from query params or session
    processing_id = request.args.get('processing_id') or session.get('processing_id')
    
    # Log for debugging
    logger.info(f"Checking status for processing_id: {processing_id}")
    logger.info(f"Available processing_results keys: {list(processing_results.keys())}")
    
    if not processing_id:
        logger.warning("No processing_id found in request or session")
        return jsonify({'status': 'error', 'message': 'No processing ID found'})
    
    # Store processing_id in session to maintain state
    session['processing_id'] = processing_id
    
    # Check if processing result exists
    if processing_id in processing_results:
        result = processing_results[processing_id]
        status = result.get('status', 'processing')
        
        logger.info(f"Processing status for {processing_id}: {status}")
        
        # If complete, load and store the results
        if status == 'complete':
            processing_data = load_processing_data(processing_id)
            
            if processing_data:
                logger.info(f"Processing data loaded for {processing_id}")
                
                # Ensure session is permanent and saved
                session.permanent = True
                
                # Store essential data in session
                session['swagger_info'] = processing_data.get('swagger_info', {})
                session['landing_page_content'] = processing_data.get('landing_page_content', '')
                session['document_links'] = processing_data.get('document_links', [])
                session['processing_status'] = 'complete'
                session['swagger_url'] = processing_data.get('swagger_url', '')
                session['landing_page_url'] = processing_data.get('landing_page_url', '')
                
                logger.info(f"URLs stored in session - swagger: {session['swagger_url']}, landing: {session['landing_page_url']}")
                logger.info(f"Session data set - keys: {list(session.keys())}")
                
                # Store address data if available
                if 'address_data' in processing_data:
                    session['address_data'] = processing_data['address_data']
                
                # Handle agents separately due to potential size
                if 'agents' in processing_data:
                    agents_temp_id = str(uuid.uuid4())
                    save_processing_data(agents_temp_id, {'agents': processing_data['agents']})
                    session['agents_temp_id'] = agents_temp_id
                
                # Store agents error if applicable
                if 'agents_error' in processing_data:
                    session['agents_error'] = processing_data['agents_error']
                
                # Clean up
                del processing_results[processing_id]
                
                return jsonify({'status': 'complete'})
            else:
                logger.error(f"Failed to load processing data for {processing_id}")
                return jsonify({'status': 'error', 'message': 'Failed to load processing data'})
                
        elif status == 'error':
            error_msg = result.get('error', 'Unknown error')
            logger.error(f"Processing error for {processing_id}: {error_msg}")
            
            session['processing_status'] = 'error'
            session['processing_error'] = error_msg
            
            # Clean up
            del processing_results[processing_id]
            
            return jsonify({'status': 'error', 'message': error_msg})
        else:
            return jsonify({'status': 'processing'})
    
    # If we can't find the processing_id in our tracking dictionary
    logger.warning(f"Processing ID {processing_id} not found in processing_results")
    # Clear the processing_id from session since it's stale
    if 'processing_id' in session:
        del session['processing_id']
    return jsonify({'status': 'error', 'message': 'Processing session expired. Please start over.'})  # Return error to force restart

@app.route('/ai')
def ai():
    # Debug: Log session data
    logger.info(f"[/ai] Session keys: {list(session.keys())}")
    logger.info(f"[/ai] swagger_url in session: {'swagger_url' in session}")
    logger.info(f"[/ai] processing_status: {session.get('processing_status')}")
    
    # Check if we have the necessary data in the session
    if 'swagger_url' not in session:
        logger.warning("[/ai] swagger_url not in session, redirecting to /url")
        flash("Please start from step 1.", "warning")
        return redirect(url_for('url'))
    
    # Check if processing is complete
    if session.get('processing_status') != 'complete':
        logger.warning(f"[/ai] processing_status is '{session.get('processing_status')}', redirecting to /loading")
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
                          license_options=license_options)

@app.route('/generate', methods=['POST'])
def generate():
    swagger_url = session.get('swagger_url')
    landing_page_url = session.get('landing_page_url')
    landing_page_content = session.get('landing_page_content', '')

    if not swagger_url:
        return jsonify({"error": "No swagger URL provided. Please go back to step 1."})

    # Import generate function from utils
    from utils.openai_utils import generate_api_description

    # Call OpenAI to generate the API description
    try:
        generated_content = generate_api_description(
            swagger_url=swagger_url,
            landing_page_url=landing_page_url,
            landing_page_content=landing_page_content
        )
    except Exception as e:
        return jsonify({"error": f"Failed to generate content: {str(e)}"})

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
                          contact_point=contact_point)

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

    # Store the latest JSON in session for download and API submission
    session['latest_json_data'] = json_data

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
        json_preview=json_preview
    )

@app.route('/download_json', methods=['GET', 'POST'])
def download_json():
    # Use the latest generated JSON from session if available
    json_data = session.get('latest_json_data')
    if not json_data:
        # Fallback: regenerate if not present
        translations = session.get('translations', {})
        theme_codes = session.get('theme_codes', [])
        selected_agency = session.get('selected_agency', '')
        access_rights_code = session.get('access_rights_code', 'PUBLIC')
        license_code = session.get('license_code', '')
        agents = get_cached_agents()
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
        document_links = session.get('document_links', [])
        from utils.json_utils import generate_dcat_json
        json_data = generate_dcat_json(
            translations=translations,
            theme_codes=theme_codes,
            agency_id=selected_agency,
            swagger_url=session.get('swagger_url', ''),
            landing_page_url=session.get('landing_page_url', ''),
            agents_list=agents,
            access_rights_code=access_rights_code,
            license_code=license_code,
            contact_point_override=contact_point,
            document_links=document_links
        )

    # Convert to pretty JSON string
    import json
    json_string = json.dumps(json_data, indent=2)

    # Create a response with the JSON data
    import io
    from flask import send_file

    # Generate filename based on the API title
    api_title = json_data.get('title', {}).get('en', 'api')
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

@app.route('/submit_to_i14y', methods=['POST'])
def submit_to_i14y():
    """
    Submit the generated JSON data directly to the I14Y API
    """
    try:
        # Use the latest generated JSON from session if available
        json_data = session.get('latest_json_data')
        if not json_data:
            # Fallback: regenerate if not present
            translations = session.get('translations', {})
            theme_codes = session.get('theme_codes', [])
            selected_agency = session.get('selected_agency', '')
            swagger_url = session.get('swagger_url', '')
            landing_page_url = session.get('landing_page_url', '')
            access_rights_code = session.get('access_rights_code', 'PUBLIC')
            license_code = session.get('license_code', '')
            agents = get_cached_agents()
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
            document_links = session.get('document_links', [])
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

        # Always get document_links from session
        document_links = session.get('document_links', [])

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
            contact_point_override=contact_point,
            document_links=document_links
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
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask app on port {port}")
    app.run(debug=False, host='0.0.0.0', port=port)