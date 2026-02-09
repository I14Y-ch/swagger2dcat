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
            static_url_path='/static',
            template_folder='templates'
           )

# Session cleanup utility
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

# Clean up old sessions before initializing session interface
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
        # Get agents without fetching details by default for better performance
        agents = get_agents(fetch_details=False)
        
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
    # Validate processing_id to prevent path injection
    import re
    if not re.match(r'^[a-f0-9-]+$', processing_id):
        logger.error(f"Invalid processing_id format: rejected for security")
        return False
    
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(temp_dir, f"swagger2dcat_{processing_id}.pkl")
    
    # Ensure the file path is within temp directory (prevent path traversal)
    real_temp_dir = os.path.realpath(temp_dir)
    real_temp_file = os.path.realpath(temp_file)
    if not real_temp_file.startswith(real_temp_dir):
        logger.error("Path traversal attempt detected")
        return False
    
    try:
        with open(temp_file, 'wb') as f:
            pickle.dump(data, f)
        return True
    except Exception as e:
        logger.error(f"Error saving processing data: {str(e)}")
        return False

def load_processing_data(processing_id):
    """Load processing data from temporary file"""
    # Validate processing_id to prevent path injection
    import re
    if not re.match(r'^[a-f0-9-]+$', processing_id):
        logger.error(f"Invalid processing_id format: rejected for security")
        return None
    
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(temp_dir, f"swagger2dcat_{processing_id}.pkl")
    
    # Ensure the file path is within temp directory (prevent path traversal)
    real_temp_dir = os.path.realpath(temp_dir)
    real_temp_file = os.path.realpath(temp_file)
    if not real_temp_file.startswith(real_temp_dir):
        logger.error("Path traversal attempt detected")
        return None
    
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
                # Record start time for performance tracking
                process_start_time = time.time()
                
                # Update progress
                if proc_id in processing_results:
                    processing_results[proc_id]['progress'] = {
                        'current_step': 'Parsing Swagger definition',
                        'percent': 15,
                        'steps_completed': 0,
                        'total_steps': 3
                    }
                
                # Step 1: Parse Swagger specification (with URL detection)
                from utils.swagger_utils import extract_swagger_info, is_likely_json_url
                
                # Check if direct JSON URL to log that we're skipping detection
                if is_likely_json_url(swagger_url):
                    logger.info(f"Direct JSON URL detected, skipping URL discovery step: {swagger_url}")
                
                swagger_info = extract_swagger_info(swagger_url)
                swagger_time = time.time() - process_start_time
                
                # Update progress after swagger parsing
                if proc_id in processing_results:
                    processing_results[proc_id]['progress'] = {
                        'current_step': 'Swagger definition parsed',
                        'percent': 40,
                        'steps_completed': 1,
                        'total_steps': 3
                    }
                
                # Log performance metrics
                logger.info(f"Swagger parsing completed in {swagger_time:.2f} seconds")
                if swagger_info.get('processing_time'):
                    logger.info(f"Swagger internal processing time: {swagger_info.get('processing_time')} seconds")
                
                # Flag for special information about the URL processing
                if swagger_info.get('direct_json'):
                    logger.info(f"Direct JSON URL processed without detection step: {swagger_url}")
                elif swagger_info.get('url_detected'):
                    logger.info(f"JSON URL auto-detected: {swagger_info.get('resolved_url')}")
                
                # Step 2: Extract content from landing page if provided
                landing_page_content = ""
                document_links = []
                address_data = {}
                
                # Update progress to landing page step
                if proc_id in processing_results:
                    processing_results[proc_id]['progress'] = {
                        'current_step': 'Processing landing page',
                        'percent': 50,
                        'steps_completed': 1,
                        'total_steps': 3
                    }
                
                # Skip landing page processing if URL is empty
                if landing_page_url and landing_page_url.strip():
                    landing_start_time = time.time()
                    from utils.web_utils import extract_web_content
                    # Returns address_data as 5th value
                    web_title, web_description, web_content, doc_links, address_data = extract_web_content(landing_page_url)
                    
                    if web_content:
                        landing_page_content = web_description or web_content
                        document_links = doc_links
                    
                    landing_time = time.time() - landing_start_time
                    logger.info(f"Landing page processing completed in {landing_time:.2f} seconds")
                    logger.info(f"Extracted {len(document_links)} document links from landing page")
                else:
                    logger.info("Skipping landing page processing (no URL provided)")
                
                # Update progress after landing page
                if proc_id in processing_results:
                    processing_results[proc_id]['progress'] = {
                        'current_step': 'Loading metadata',
                        'percent': 75,
                        'steps_completed': 2,
                        'total_steps': 3
                    }
                
                # Step 3: Get the list of agents (using cache)
                agents_start_time = time.time()
                agents = get_cached_agents()
                agents_error = None if agents else "Failed to fetch agents"
                agents_time = time.time() - agents_start_time
                logger.info(f"Agents fetching completed in {agents_time:.2f} seconds")
                
                # Calculate total processing time
                total_time = time.time() - process_start_time
                logger.info(f"Total processing completed in {total_time:.2f} seconds")
                
                # Update progress to 99% (final steps)
                if proc_id in processing_results:
                    processing_results[proc_id]['progress'] = {
                        'current_step': 'Finalizing',
                        'percent': 99,
                        'steps_completed': 3,
                        'total_steps': 3
                    }
                
                # Save processing data to temporary file
                processing_data = {
                    'swagger_info': swagger_info,
                    'landing_page_content': landing_page_content,
                    'document_links': document_links,
                    'agents': agents,
                    'agents_error': agents_error,
                    'address_data': address_data,
                    'swagger_url': swagger_url,
                    'landing_page_url': landing_page_url,
                    'processing_metrics': {
                        'swagger_time': swagger_time,
                        'total_time': total_time,
                        'direct_json': swagger_info.get('direct_json', False),
                        'url_detected': swagger_info.get('url_detected', False)
                    }
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
        
        # Initialize progress tracking
        processing_results[workflow_id]['progress'] = {
            'current_step': 'Initializing...',
            'percent': 5,
            'steps_completed': 0,
            'total_steps': 3
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
    Improved processing status check that provides more detailed progress information
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
        
        # If processing, include any progress information
        if status == 'processing':
            # Return progress information if available
            progress = result.get('progress', {})
            step = progress.get('current_step', 'Analyzing API information')
            percent = progress.get('percent', 25)  # Default progress value
            
            return jsonify({
                'status': 'processing',
                'progress': {
                    'step': step,
                    'percent': percent,
                    'message': f"Processing: {step}"
                }
            })
        
        # If complete, load and store the results
        elif status == 'complete':
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
                
                # Store processing metrics if available
                if 'processing_metrics' in processing_data:
                    session['processing_metrics'] = processing_data['processing_metrics']
                
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
    
    # Import session utilities for file-based storage
    from utils.session_utils import save_to_session_file, load_from_session_file, restore_all_data_from_files
    
    # Restore all data from persistent storage for Docker reliability
    restore_all_data_from_files()
    
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

    # Get any previously entered or generated content (prioritize API details, then generated, then swagger)
    title = session.get('title') or session.get('generated_title', '') or swagger_info.get('title', '')
    description = session.get('description') or session.get('generated_description', '') or full_description
    
    # Get keywords from session, generated, or Swagger
    keywords = session.get('keywords') or session.get('generated_keywords', []) or swagger_info.get('keywords', [])
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
    from utils.session_utils import save_to_session_file
    
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

    # IMPORTANT: Save generated content to persistent storage for Docker reliability
    generated_data = {
        'generated_title': session['generated_title'],
        'generated_description': session['generated_description'],
        'generated_keywords': session['generated_keywords'],
        'theme_codes': session['theme_codes']
    }
    save_to_session_file('generated_content', generated_data)
    logger.info("Saved generated content to persistent storage")

    return jsonify(generated_content)

@app.route('/upload', methods=['GET'])
def upload():
    # Check if we have the basic data in the session
    if 'swagger_url' not in session:
        flash("Please start from step 1.", "warning")
        return redirect(url_for('url'))
    
    # Import our session utilities
    from utils.session_utils import save_to_session_file, load_from_session_file, restore_all_data_from_files
    from utils.i14y_utils import get_agents

    # Restore all data from persistent storage for Docker reliability
    restore_all_data_from_files()
    
    # Get restored API details for fallback values
    api_details = load_from_session_file('api_details', {})
    
    # Get agents for publisher name resolution
    agents = get_cached_agents()
    theme_codes = session.get('theme_codes', [])
    selected_agency = session.get('selected_agency', '')
    access_rights_code = session.get('access_rights_code', 'PUBLIC')
    license_code = session.get('license_code', '')
    swagger_url = session.get('swagger_url', '')
    landing_page_url = session.get('landing_page_url', '')
    document_links = session.get('document_links', [])

    # Always try to load translations from file first with enhanced logging
    logger.info(f"[/upload] Session ID: {session.get('_id', 'NO_ID')}")
    logger.info("[/upload] Attempting to load translations from file...")
    translations = load_from_session_file('translations', {})
    logger.info(f"[/upload] File load result: {bool(translations)} ({len(translations) if translations else 0} languages)")
    
    # Log what we found in the file
    if translations:
        for lang, content in translations.items():
            title = content.get('title', '') if isinstance(content, dict) else ''
            logger.info(f"[/upload] File {lang}: title='{title[:30]}...' ({len(title)} chars)")
    
    # If file didn't have good data, check session
    if not translations or not any(
        isinstance(lang_data, dict) and (lang_data.get('title') or lang_data.get('description'))
        for lang_data in translations.values()
    ):
        logger.info("[/upload] File load failed or empty, checking session...")
        session_translations = session.get('translations', {})
        logger.info(f"[/upload] Session load result: {bool(session_translations)} ({len(session_translations) if session_translations else 0} languages)")
        
        # Use session data if it's better than file data
        if session_translations and any(
            isinstance(lang_data, dict) and (lang_data.get('title') or lang_data.get('description'))
            for lang_data in session_translations.values()
        ):
            translations = session_translations
            logger.info("[/upload] Using session translations (better than file)")
    
    # If still not found, fallback to API details or generated content
    if not translations or not any(
        isinstance(lang_data, dict) and (lang_data.get('title') or lang_data.get('description'))
        for lang_data in translations.values()
    ):
        logger.info("[/upload] No good translations found, attempting to create from API details...")
        # Try to get from API details or generated content
        title = session.get('title') or session.get('generated_title', '') or api_details.get('title', '')
        description = session.get('description') or session.get('generated_description', '') or api_details.get('description', '')
        keywords = session.get('keywords') or session.get('generated_keywords', []) or api_details.get('keywords', [])
        
        logger.info(f"[/upload] Fallback data: title='{title[:50] if title else 'NONE'}...', desc_len={len(description) if description else 0}")
        
        if title or description:
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
            save_to_session_file('translations', translations)
            session['translations_available'] = True
            logger.info("Created fallback translations structure")
        else:
            logger.error("[/upload] No translation data available anywhere!")
            flash("Please complete the API details step first.", "warning")
            return redirect(url_for('ai'))
    
    # Ensure session is always updated with the current translations
    session['translations'] = translations
    session['translations_available'] = True
    
    logger.info(f"[/upload] Loaded translations with keys: {list(translations.keys()) if translations else 'None'}")
    if translations:
        logger.info(f"[/upload] EN title: '{translations.get('en', {}).get('title', 'MISSING')[:50]}...'")
        logger.info(f"[/upload] DE title: '{translations.get('de', {}).get('title', 'MISSING')[:50]}...'")
        logger.info(f"[/upload] FR title: '{translations.get('fr', {}).get('title', 'MISSING')[:50]}...'")
        logger.info(f"[/upload] IT title: '{translations.get('it', {}).get('title', 'MISSING')[:50]}...'")
    
    # Initialize default contact point structure if missing
    default_contact_point = {
        "fn": {"de": "", "en": "", "fr": "", "it": "", "rm": ""},
        "hasAddress": {"de": "", "en": "", "fr": "", "it": "", "rm": ""},
        "hasEmail": "",
        "hasTelephone": "",
        "kind": "Organization",
        "note": {"de": "", "en": "", "fr": "", "it": "", "rm": ""}
    }
    
    # Backward compatibility for template - provide 'org' and 'adrWork' for templates
    template_contact_point = {
        "org": {"de": "", "en": "", "fr": "", "it": ""},
        "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
        "emailInternet": "",
        "telWorkVoice": "",
        "note": {"de": "", "en": "", "fr": "", "it": ""}
    }

    # Load address_data from session if available (set in check_processing_status)
    address_data = session.get('address_data', {})
    
    # Fetch detailed agency information if we have a selected agency
    agency_details = {}
    if selected_agency:
        logger.info(f"[/upload] Fetching details for agency ID: {selected_agency}")
        try:
            import requests
            response = requests.get(
                f"https://input.i14y.admin.ch/api/Agent/{selected_agency}",
                timeout=5
            )
            if response.status_code == 200:
                agency_details = response.json()
                logger.info(f"[/upload] Successfully fetched agency details for {agency_details.get('id')}")
                
                # If we have contact information from the agency, create an address_data structure
                if agency_details.get('contactPoint'):
                    cp = agency_details['contactPoint']
                    # Only overwrite address_data if it's empty
                    if not address_data:
                        address_data = {
                            'address': cp.get('hasAddress', {}).get('en', ''),
                            'email': cp.get('hasEmail', ''),
                            'phone': cp.get('hasTelephone', ''),
                            'organization': agency_details.get('name', {}).get('en', ''),
                            'note': cp.get('note', '')
                        }
                        session['address_data'] = address_data
                        logger.info(f"[/upload] Created address_data from agency details")
        except Exception as e:
            logger.error(f"[/upload] Error fetching agency details: {str(e)}")
    
    # Allow editing of all fields
    if request.method == 'POST':
        # Get contact point from session or use default
        contact_point = session.get('contact_point', default_contact_point)        # Remove fn field if present - not needed for I14Y API
        if 'fn' in contact_point:
            del contact_point['fn']
                
        # Validate required fields
        email = request.form.get('emailInternet', '').strip()
        if not email:
            flash("Email address is required.", "danger")
            return redirect(url_for('upload'))
        
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

        contact_point['fn']['de'] = request.form.get('org_de', '')
        contact_point['fn']['en'] = request.form.get('org_en', '')
        contact_point['fn']['fr'] = request.form.get('org_fr', '')
        contact_point['fn']['it'] = request.form.get('org_it', '')
        contact_point['fn']['rm'] = ""
        contact_point['hasAddress']['de'] = request.form.get('adr_de', '')
        contact_point['hasAddress']['en'] = request.form.get('adr_en', '')
        contact_point['hasAddress']['fr'] = request.form.get('adr_fr', '')
        contact_point['hasAddress']['it'] = request.form.get('adr_it', '')
        contact_point['hasAddress']['rm'] = ""
        contact_point['hasEmail'] = request.form.get('emailInternet', '')
        contact_point['hasTelephone'] = request.form.get('telWorkVoice', '')
        contact_point['note']['de'] = request.form.get('note_de', '')
        contact_point['note']['en'] = request.form.get('note_en', '')
        contact_point['note']['fr'] = request.form.get('note_fr', '')
        contact_point['note']['it'] = request.form.get('note_it', '')
        contact_point['note']['rm'] = ""
        contact_point['kind'] = "Organization"

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
        # Ensure backward compatibility with template fields
        if 'org' not in contact_point:
            contact_point['org'] = {"de": "", "en": "", "fr": "", "it": ""}
        if 'adrWork' not in contact_point:
            contact_point['adrWork'] = {"de": "", "en": "", "fr": "", "it": ""}
        if 'emailInternet' not in contact_point:
            contact_point['emailInternet'] = contact_point.get('hasEmail', '')
        if 'telWorkVoice' not in contact_point:
            contact_point['telWorkVoice'] = contact_point.get('hasTelephone', '')
            
        # Copy from fn to org for compatibility if fn exists
        if 'fn' in contact_point:
            for lang in ['de', 'en', 'fr', 'it']:
                if contact_point['fn'].get(lang):
                    contact_point['org'][lang] = contact_point['fn'].get(lang, '')

        # --- Prefill contact point fields from address_data if available and fields are empty ---
        if address_data:
            # Prefill email
            if not contact_point.get('emailInternet'):
                contact_point['emailInternet'] = address_data.get('email', '')
            # Prefill phone
            if not contact_point.get('telWorkVoice'):
                contact_point['telWorkVoice'] = address_data.get('phone', '')
            
            # Prefill org name in all languages from agency_details if available
            if agency_details and agency_details.get('name'):
                for lang in ['de', 'en', 'fr', 'it']:
                    if not contact_point['org'].get(lang) and agency_details['name'].get(lang):
                        contact_point['org'][lang] = agency_details['name'].get(lang, '')
            # If no agency_details, fall back to single org name from address_data
            elif address_data.get('organization'):
                org_name = address_data.get('organization', '')
                for lang in ['de', 'en', 'fr', 'it']:
                    if not contact_point['org'].get(lang):
                        contact_point['org'][lang] = org_name
            
            # Prefill address in all languages if available from agency_details
            if agency_details and agency_details.get('contactPoint') and agency_details['contactPoint'].get('hasAddress'):
                for lang in ['de', 'en', 'fr', 'it']:
                    if not contact_point['adrWork'].get(lang) and agency_details['contactPoint']['hasAddress'].get(lang):
                        contact_point['adrWork'][lang] = agency_details['contactPoint']['hasAddress'].get(lang, '')
            # Fall back to single address from address_data
            elif address_data.get('address'):
                adr = address_data.get('address', '')
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
    # Use agency identifier if available
    publisher_identifier = agency_details.get('identifier', selected_agency)
    
    # Make a copy of contact_point with appropriate structure for json_utils
    json_contact_point = {
        "fn": contact_point.get('fn', contact_point.get('org', {})),
        "hasAddress": contact_point.get('hasAddress', contact_point.get('adrWork', {})),
        "hasEmail": contact_point.get('hasEmail', contact_point.get('emailInternet', '')),
        "hasTelephone": contact_point.get('hasTelephone', contact_point.get('telWorkVoice', '')),
        "kind": "Organization",
        "note": contact_point.get('note', {})
    }
    
    json_data = generate_dcat_json(
        translations=translations,
        theme_codes=theme_codes,
        agency_id=publisher_identifier,
        swagger_url=swagger_url,
        landing_page_url=landing_page_url,
        agents_list=agents,
        access_rights_code=access_rights_code,
        license_code=license_code,
        contact_point_override=json_contact_point,
        document_links=document_links
    )
    json_preview = json.dumps(json_data, indent=2)

    # Store the latest JSON in session for download and API submission
    session['latest_json_data'] = json_data

    # Render the template with editable fields
    logger.info(f"[/upload] Rendering template with translations keys: {list(translations.keys()) if translations else 'None'}")
    logger.info(f"[/upload] English title: '{translations.get('en', {}).get('title', 'MISSING')[:50]}...' if translations else 'No translations'")
    logger.info(f"[/upload] Contact point org en: '{contact_point.get('org', {}).get('en', 'MISSING')}'")
    
    # Compatibility mapping for template
    template_contact_point = contact_point.copy()
    template_contact_point['org'] = {
        'de': contact_point.get('fn', {}).get('de', ''),
        'en': contact_point.get('fn', {}).get('en', ''),
        'fr': contact_point.get('fn', {}).get('fr', ''),
        'it': contact_point.get('fn', {}).get('it', '')
    }
    template_contact_point['adrWork'] = {
        'de': contact_point.get('hasAddress', {}).get('de', ''),
        'en': contact_point.get('hasAddress', {}).get('en', ''),
        'fr': contact_point.get('hasAddress', {}).get('fr', ''),
        'it': contact_point.get('hasAddress', {}).get('it', '')
    }
    template_contact_point['emailInternet'] = contact_point.get('hasEmail', '')
    template_contact_point['telWorkVoice'] = contact_point.get('hasTelephone', '')
    return render_template('upload.html',
        translations=translations,
        contact_point=template_contact_point,
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
            "telWorkVoice": ""
        }
        contact_point = session.get('contact_point', default_contact_point)
        
        default_contact_point = {
            "emailInternet": "",
            "org": {"de": "", "en": "", "fr": "", "it": ""},
            "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
            "note": {"de": "", "en": "", "fr": "", "it": ""},
            "telWorkVoice": ""
        }
        contact_point = session.get('contact_point', default_contact_point)
        
        # Remove fn field if present as it's not expected in the I14Y API schema
        if 'fn' in contact_point:
            del contact_point['fn']
        document_links = session.get('document_links', [])
        
        # Fetch agency details to get the correct identifier
        agency_identifier = selected_agency
        try:
            import requests
            response = requests.get(
                f"https://input.i14y.admin.ch/api/Agent/{selected_agency}",
                timeout=5
            )
            if response.status_code == 200:
                agency_details = response.json()
                agency_identifier = agency_details.get('identifier', selected_agency)
        except Exception as e:
            pass
        
        # Make a copy of contact_point with appropriate structure for json_utils
        json_contact_point = {
            "fn": contact_point.get('org', {}),
            "hasAddress": contact_point.get('adrWork', {}),
            "hasEmail": email or contact_point.get('emailInternet', ''),  # Use email from request if available
            "hasTelephone": contact_point.get('telWorkVoice', ''),
            "kind": "Organization",
            "note": contact_point.get('note', {})
        }

        from utils.json_utils import generate_dcat_json
        json_data = generate_dcat_json(
            translations=translations,
            theme_codes=theme_codes,
            agency_id=agency_identifier,
            swagger_url=session.get('swagger_url', ''),
            landing_page_url=session.get('landing_page_url', ''),
            agents_list=agents,
            access_rights_code=access_rights_code,
            license_code=license_code,
            contact_point_override=json_contact_point,
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
    Submit the generated JSON data directly to the I14Y Partner API
    """
    try:
        # Import session utilities for data restoration
        from utils.session_utils import restore_all_data_from_files
        
        # Ensure all data is restored from persistent storage (Docker reliability)
        restore_all_data_from_files()
        
        # Use the latest generated JSON from session if available
        json_data = session.get('latest_json_data')
        if not json_data:
            # Fallback: regenerate if not present
            from utils.session_utils import load_from_session_file
            
            # Load data from both session and files (prefer session, fallback to files)
            translations = session.get('translations', {}) or load_from_session_file('translations', {})
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

        # Get the token and email from request
        request_data = request.get_json()
        if not request_data or 'token' not in request_data:
            return jsonify({
                'success': False, 
                'error': 'No access token provided.'
            })
        
        token = request_data['token'].strip()
        email = request_data.get('email', '')
        
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

        # Get contact point from session or use default
        default_contact_point = {
            "emailInternet": "",
            "org": {"de": "", "en": "", "fr": "", "it": ""},
            "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
            "note": {"de": "", "en": "", "fr": "", "it": ""},
            "telWorkVoice": ""
        }
        contact_point = session.get('contact_point', default_contact_point)
        
        # Ensure contact_point is a dictionary
        if not isinstance(contact_point, dict):
            logger.warning(f"[submit_to_i14y] contact_point is not a dictionary: {type(contact_point)}")
            contact_point = default_contact_point.copy()
        
        # Check if email is present and ensure it's properly set
        email = request_data.get('email', contact_point.get('emailInternet', ''))
        if email:
            contact_point['emailInternet'] = email
        
        # If emailInternet is empty but we have input email, use it
        if not contact_point.get('emailInternet') and email:
            contact_point['emailInternet'] = email
            
        # Remove the fn field if present - it's not expected by the I14Y API schema
        if 'fn' in contact_point:
            del contact_point['fn']
            
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
        
        # Ensure the email is set correctly in the JSON payload
        if json_data.get('contactPoints') and isinstance(json_data['contactPoints'], list) and json_data['contactPoints']:
            # Force the hasEmail field to the user-provided email
            if email:
                json_data['contactPoints'][0]['hasEmail'] = email

        # Submit to I14Y API
        try:
            logger.info(f"[submit_to_i14y] Calling submit_data_to_i14y_api with JSON data")
            logger.info(f"[submit_to_i14y] Type of contact_point: {type(contact_point)}")
            for key, value in contact_point.items():
                logger.info(f"[submit_to_i14y] contact_point[{key}] = {type(value)}")
            
            i14y_response = submit_data_to_i14y_api(json_data, token)
            
            if i14y_response.get('success', False):
                    guid = i14y_response.get('dataset_id', '')
                    catalog_url = f"https://input.i14y.admin.ch/catalog/dataservices/{guid}/description?backto=dataservices" if guid else None
                    return jsonify({
                        'success': True,
                        'message': 'Successfully submitted to I14Y API',
                        'dataset_id': guid,
                        'catalog_url': catalog_url
                    })
            else:
                error_response = {
                    'success': False,
                    'error': i14y_response['error']
                }
                
                # Include full error details if available
                if 'full_error' in i14y_response:
                    error_response['full_error'] = i14y_response['full_error']
                    
                return jsonify(error_response)
        except Exception as e:
            logger.error(f"[submit_to_i14y] Error during API submission: {str(e)}")
            import traceback
            logger.error(f"[submit_to_i14y] Traceback: {traceback.format_exc()}")
            return jsonify({
                'success': False,
                'error': f'Error during API submission: {str(e)}'
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        })

def submit_data_to_i14y_api(json_data, token):
    """
    Submit data to the I14Y Partner API endpoint
    
    Args:
        json_data (dict): The DCAT JSON data to submit
        token (str): The Bearer token for authentication
    
    Returns:
        dict: Response with success status and details
    """
    try:
        # I14Y Partner API endpoint (new API)
        api_endpoint = "https://api.i14y.admin.ch/api/partner/v1/dataservices"
        
        # Prepare headers
        headers = {
            'Authorization': token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # Debug info - log structure but not sensitive data
        logger.info(f"[submit_data_to_i14y_api] Publisher identifier: {json_data.get('publisher', {}).get('identifier')}")
        logger.info(f"[submit_data_to_i14y_api] Contact points count: {len(json_data.get('contactPoints', []))}")
        if json_data.get('contactPoints'):
            logger.info(f"[submit_data_to_i14y_api] Contact point structure validated")
        
        # Wrap the JSON data in a "data" field as required by the Partner API
        # Also include the 'input' field required by the Partner API
        wrapped_payload = {
            "data": json_data
        }
        # Don't print full json_data as it may contain sensitive information
        logger.debug(f"[submit_data_to_i14y_api] Payload prepared for submission")
        # Make the API request with timeout
        response = requests.post(
            api_endpoint,
            json=wrapped_payload,
            headers=headers,
            timeout=30  # 30 second timeout
        )
        
        # Check if request was successful
        if response.status_code == 200 or response.status_code == 201:
            try:
                # The I14Y Partner API returns a UUID string directly for successful submissions
                response_text = response.text.strip().strip('"')
                
                # Check if response looks like a UUID (basic validation)
                uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
                if uuid_pattern.match(response_text):
                    # It's a valid UUID
                    return {
                        'success': True,
                        'dataset_id': response_text
                    }
                    
                # Fallback to parsing as JSON if it's not a plain UUID string
                response_data = response.json()
                dataset_id = response_data.get('id') or response_data.get('datasetId') or 'Generated'
                return {
                    'success': True,
                    'dataset_id': dataset_id,
                    'response': response_data
                }
            except json.JSONDecodeError:
                # If it's not valid JSON but the status is success, use the response text as dataset_id
                # This handles the case where the API returns just the UUID as plain text
                return {
                    'success': True,
                    'dataset_id': response.text.strip().strip('"'),
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
                # Get detailed error info, checking different possible structures
                error_detail = ''
                if 'detail' in error_data:
                    error_detail = f"\nDetail: {json.dumps(error_data['detail'], indent=2)}"
                elif 'errors' in error_data:
                    error_detail = f"\nErrors: {json.dumps(error_data['errors'], indent=2)}"
                elif 'validationErrors' in error_data:
                    error_detail = f"\nValidation Errors: {json.dumps(error_data['validationErrors'], indent=2)}"
                
                print(f"I14Y Partner API Error Response: {json.dumps(error_data, indent=2)}")
                
                return {
                    'success': False,
                    'error': f'Partner API error: {error_msg}{error_detail}',
                    'full_error': error_data
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
                
                # Get detailed validation errors
                error_detail = ''
                if 'detail' in error_data:
                    error_detail = f"\nDetail: {json.dumps(error_data['detail'], indent=2)}"
                elif 'errors' in error_data:
                    error_detail = f"\nErrors: {json.dumps(error_data['errors'], indent=2)}"
                elif 'validationErrors' in error_data:
                    error_detail = f"\nValidation Errors: {json.dumps(error_data['validationErrors'], indent=2)}"
                
                print(f"I14Y Partner API Validation Error: {json.dumps(error_data, indent=2)}")
                
                return {
                    'success': False,
                    'error': f'Data validation failed (Partner API): {error_msg}{error_detail}',
                    'full_error': error_data
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
                
                # Get detailed error info for any response format
                error_detail = ''
                if 'detail' in error_data:
                    error_detail = f"\nDetail: {json.dumps(error_data['detail'], indent=2)}"
                elif 'errors' in error_data:
                    error_detail = f"\nErrors: {json.dumps(error_data['errors'], indent=2)}"
                elif 'validationErrors' in error_data:
                    error_detail = f"\nValidation Errors: {json.dumps(error_data['validationErrors'], indent=2)}"
                
                print(f"I14Y Partner API Error (HTTP {response.status_code}): {json.dumps(error_data, indent=2)}")
                
                return {
                    'success': False,
                    'error': f'Partner API error: {error_msg}{error_detail}',
                    'full_error': error_data
                }
            except json.JSONDecodeError:
                return {
                    'success': False,
                    'error': f'API error (HTTP {response.status_code}): {response.text}'
                }
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'error': 'Request timed out. The I14Y Partner API may be temporarily unavailable.'
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

@app.route('/debug_i14y_json', methods=['GET'])
def debug_i14y_json():
    """Debug endpoint to view the exact JSON that would be sent to I14Y Partner API"""
    try:
        # Import session utilities for data restoration
        from utils.session_utils import restore_all_data_from_files
        
        # Ensure all data is restored from persistent storage
        restore_all_data_from_files()
        
        # Load required data
        from utils.session_utils import load_from_session_file
        translations = session.get('translations', {}) or load_from_session_file('translations', {})
        theme_codes = session.get('theme_codes', [])
        selected_agency = session.get('selected_agency', '')
        swagger_url = session.get('swagger_url', '')
        landing_page_url = session.get('landing_page_url', '')
        access_rights_code = session.get('access_rights_code', 'PUBLIC')
        license_code = session.get('license_code', '')
        access_rights_code = session.get('access_rights_code', 'PUBLIC')
        license_code = session.get('license_code', '')
        agents = get_cached_agents()
        
        # Get contact point data
        default_contact_point = {
            "emailInternet": "",
            "org": {"de": "", "en": "", "fr": "", "it": ""},
            "adrWork": {"de": "", "en": "", "fr": "", "it": ""},
            "note": {"de": "", "en": "", "fr": "", "it": ""},
            "telWorkVoice": ""
        }
        contact_point = session.get('contact_point', default_contact_point)
        # Remove fn field if present
        if 'fn' in contact_point:
            del contact_point['fn']
            
        document_links = session.get('document_links', [])
        
        # Fetch agency details to get the correct identifier
        agency_identifier = selected_agency
        try:
            import requests
            response = requests.get(
                f"https://input.i14y.admin.ch/api/Agent/{selected_agency}",
                timeout=5
            )
            if response.status_code == 200:
                agency_details = response.json()
                agency_identifier = agency_details.get('identifier', selected_agency)
        except Exception as e:
            pass
        
        # Generate the JSON
        from utils.json_utils import generate_dcat_json
        json_data = generate_dcat_json(
            translations=translations,
            theme_codes=theme_codes,
            agency_id=agency_identifier,
            swagger_url=swagger_url,
            landing_page_url=landing_page_url,
            agents_list=agents,
            access_rights_code=access_rights_code,
            license_code=license_code,
            contact_point_override=contact_point,
            document_links=document_links
        )
        
        # For debugging, let's see if we can detect any potential issues in the data
        validation_notes = []
        
        # Check for empty or invalid fields
        if not selected_agency:
            validation_notes.append("WARNING: No agency/publisher selected")
        
        if 'en' not in translations or not translations['en'].get('title'):
            validation_notes.append("WARNING: Missing English title")
            
        if 'en' not in translations or not translations['en'].get('description'):
            validation_notes.append("WARNING: Missing English description")
            
        # Add contact point validation
        if not contact_point.get('emailInternet'):
            validation_notes.append("WARNING: No contact email specified")
            
        if not any(contact_point.get('org', {}).values()):
            validation_notes.append("WARNING: No organization name in contact point")
            
        # Check for the 'fn' field which is not supported by I14Y Partner API
        if 'fn' in contact_point:
            validation_notes.append("WARNING: 'fn' field found in contact_point but will be removed before submission as it's not supported by the Partner API")
        
        # Add a note about the payload wrapping required by the Partner API
        validation_notes.append("NOTE: For I14Y Partner API submission, the JSON will be wrapped in a 'data' field")
        validation_notes.append("NOTE: Publisher is formatted as { \"identifier\": \"agency_id\" } for Partner API compatibility")
        
        # Create the wrapped payload that will be sent to the Partner API
        wrapped_payload = {
            "data": json_data
        }
            
        # Create response with pretty-printed JSON for browser viewing
        response_data = {
            'json_data': json_data,
            'wrapped_payload': wrapped_payload,
            'validation_notes': validation_notes,
            'pretty_json': json.dumps(json_data, indent=2, ensure_ascii=False),
            'pretty_wrapped_payload': json.dumps(wrapped_payload, indent=2, ensure_ascii=False)
        }
        
        # Return the JSON for inspection
        return jsonify(response_data)
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        })

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

@app.route('/save_api_details', methods=['POST'])
def save_api_details():
    """
    Save API details from either the AI form or the translation step.
    Detects which form was submitted based on the presence of specific fields.
    """
    from utils.session_utils import save_to_session_file, load_from_session_file

    # Check if this is from the AI form
    is_ai_form = 'title' in request.form and 'title_en' not in request.form

    if is_ai_form:
        # Handle AI form submission - save API details to session and files
        logger.info("Saving API details from AI form")
        
        # Save basic API details to session
        session['title'] = request.form.get('title', '').strip()
        session['description'] = request.form.get('description', '').strip()
        
        # Handle keywords
        keywords_str = request.form.get('keywords', '')
        keywords = [kw.strip() for kw in keywords_str.split(',') if kw.strip()]
        session['keywords'] = keywords
        
        # Handle theme codes (multi-select)
        theme_codes = request.form.getlist('theme_codes')
        session['theme_codes'] = theme_codes
        
        # Save access rights and license
        session['access_rights_code'] = request.form.get('access_rights_code', 'PUBLIC')
        session['license_code'] = request.form.get('license_code', '')
        
        # Save selected agency
        session['selected_agency'] = request.form.get('agency', '')
        
        # Save API details to persistent file storage for Docker reliability
        api_details = {
            'title': session['title'],
            'description': session['description'],
            'keywords': session['keywords'],
            'theme_codes': session['theme_codes'],
            'access_rights_code': session['access_rights_code'],
            'license_code': session['license_code'],
            'selected_agency': session['selected_agency']
        }
        save_to_session_file('api_details', api_details)
        
        # Also save the current state with generated content for extra safety
        if session.get('generated_title') or session.get('generated_description'):
            generated_data = {
                'generated_title': session.get('generated_title', ''),
                'generated_description': session.get('generated_description', ''),
                'generated_keywords': session.get('generated_keywords', []),
                'theme_codes': session.get('theme_codes', [])
            }
            save_to_session_file('generated_content', generated_data)
            logger.info("Also saved generated content as backup during API details save")
        
        logger.info(f"Saved API details: title='{session['title'][:50]}...', keywords={len(keywords)}, themes={len(theme_codes)}, agency='{session['selected_agency']}'")
        
        # NEW: Auto-generate translations and go directly to review
        # Create initial translations structure with English content
        title = session['title']
        description = session['description']
        keywords = session['keywords']
        
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
        
        # Auto-translate if DeepL is available
        try:
            from utils.deepl_utils import translate_to_language
            
            logger.info(f"Starting auto-translation for title='{title[:50]}...', desc_len={len(description)}, keywords={keywords}")
            
            # Try to translate to German, French, and Italian
            for target_lang in ['de', 'fr', 'it']:
                try:
                    logger.info(f"Attempting translation to {target_lang}")
                    translated = translate_to_language(title, description, keywords, target_lang)
                    logger.info(f"Translation result for {target_lang}: {translated}")
                    
                    if translated and not translated.get('error'):
                        translations[target_lang] = {
                            'title': translated.get('title', ''),
                            'description': translated.get('description', ''),
                            'keywords': translated.get('keywords', [])
                        }
                        logger.info(f"Auto-translated content to {target_lang}: title='{translated.get('title', '')[:30]}...', desc_len={len(translated.get('description', ''))}")
                    else:
                        logger.warning(f"Translation to {target_lang} failed: {translated.get('error', 'Unknown error')}")
                except Exception as e:
                    logger.warning(f"Translation to {target_lang} failed: {str(e)}")
        except ImportError:
            logger.info("DeepL translation not available - using empty translations")
        except Exception as e:
            logger.warning(f"Auto-translation failed: {str(e)}")
        
        # Save translations to persistent storage
        session['translations'] = translations
        save_to_session_file('translations', translations)
        session['translations_available'] = True
        
        # Debug logging for translations
        logger.info("Auto-generated translations and proceeding to review")
        logger.info(f"Translations structure created with {len(translations)} languages")
        logger.info(f"Session ID: {session.get('_id', 'NO_ID')}")
        for lang, content in translations.items():
            logger.info(f"  {lang}: title='{content.get('title', '')[:50]}...', desc_len={len(content.get('description', ''))}, keywords_count={len(content.get('keywords', []))}")
        
        # Verify file save worked
        saved_translations = load_from_session_file('translations', {})
        if saved_translations:
            logger.info("Verified: translations successfully saved to file")
            logger.info(f"Loaded back: {len(saved_translations)} languages")
        else:
            logger.error("ERROR: translations NOT saved to file!")
        
    # Redirect directly to upload/review step
    return redirect(url_for('upload'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask app on port {port}")
    app.run(debug=False, host='0.0.0.0', port=port)