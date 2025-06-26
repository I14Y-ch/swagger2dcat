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

def restore_all_data_from_files():
    """Restore all persistent data from files to session (Docker reliability)"""
    import logging
    logger = logging.getLogger(__name__)
    
    # List of all data keys that should be restored
    data_keys = ['api_details', 'generated_content', 'translations']
    
    restored_keys = []
    for key in data_keys:
        data = load_from_session_file(key, {})
        if data:
            if key == 'api_details':
                # Restore API details
                for sub_key, value in data.items():
                    if sub_key not in session or not session.get(sub_key):
                        session[sub_key] = value
                        restored_keys.append(f"{key}.{sub_key}")
            elif key == 'generated_content':
                # Restore generated content
                for sub_key, value in data.items():
                    if sub_key not in session or not session.get(sub_key):
                        session[sub_key] = value
                        restored_keys.append(f"{key}.{sub_key}")
            elif key == 'translations':
                # Restore translations - be more aggressive about this
                existing_translations = session.get('translations', {})
                # Only restore if we don't have translations or if the file has more complete data
                if not existing_translations or len(data) > len(existing_translations):
                    # Check if file data is more complete (has actual content)
                    file_has_content = any(
                        lang_data.get('title') or lang_data.get('description') 
                        for lang_data in data.values() 
                        if isinstance(lang_data, dict)
                    )
                    if file_has_content or not existing_translations:
                        session['translations'] = data
                        session['translations_available'] = True
                        restored_keys.append(key)
                        logger.info(f"Restored translations from file with {len(data)} languages")
    
    if restored_keys:
        logger.info(f"Docker reliability: Restored data keys: {restored_keys}")
    
    return restored_keys
