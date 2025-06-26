import os
import json
import time
import hashlib
from flask import session

# Setup session storage directory
SESSION_STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'session_storage')

def ensure_storage_dir():
    """Ensure session storage directory exists"""
    if not os.path.exists(SESSION_STORAGE_DIR):
        os.makedirs(SESSION_STORAGE_DIR, exist_ok=True)

def get_session_file_path(key=None):
    """Get path to session storage file"""
    ensure_storage_dir()
    session_id = session.get('_id', None)
    if not session_id:
        session_id = hashlib.md5(str(time.time()).encode()).hexdigest()
        session['_id'] = session_id
    
    # If key is provided, use it for subfolder organization
    if key:
        subdir = os.path.join(SESSION_STORAGE_DIR, session_id)
        if not os.path.exists(subdir):
            os.makedirs(subdir, exist_ok=True)
        return os.path.join(subdir, f"{key}.json")
    
    return os.path.join(SESSION_STORAGE_DIR, f"{session_id}.json")

def save_to_session_file(key, data):
    """Save data to session file"""
    filepath = get_session_file_path(key)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Keep a reference in the session that this data exists on disk
    session[f"{key}_stored"] = True
    return True

def load_from_session_file(key, default=None):
    """Load data from session file"""
    filepath = get_session_file_path(key)
    if not os.path.exists(filepath):
        return default
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        # Silent error handling
        return default

def delete_session_file(key=None):
    """Delete session storage file"""
    if key:
        filepath = get_session_file_path(key)
        if os.path.exists(filepath):
            os.remove(filepath)
            if key + '_stored' in session:
                del session[key + '_stored']
    else:
        # Delete the entire session directory
        session_id = session.get('_id', None)
        if session_id:
            subdir = os.path.join(SESSION_STORAGE_DIR, session_id)
            if os.path.exists(subdir):
                import shutil
                shutil.rmtree(subdir)
            
            main_file = os.path.join(SESSION_STORAGE_DIR, f"{session_id}.json")
            if os.path.exists(main_file):
                os.remove(main_file)
