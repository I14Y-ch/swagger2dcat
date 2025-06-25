import os
import sys
import logging

def setup_environment():
    """Set up environment variables and logging for the application"""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    logger = logging.getLogger('swagger2dcat')
    
    # Check for critical environment variables
    openai_api_key = os.environ.get('OPENAI_API_KEY')
    if not openai_api_key:
        logger.warning("OPENAI_API_KEY environment variable is not set! OpenAI features will not work.")
    else:
        logger.info("OPENAI_API_KEY is set.")
    
    # Check for other environment variables
    deepl_api_key = os.environ.get('DEEPL_API_KEY')
    if not deepl_api_key:
        logger.warning("DEEPL_API_KEY environment variable is not set! Translation features will not work.")
    
    # Add the current directory to Python path to help with imports
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if current_dir not in sys.path:
        sys.path.append(current_dir)
    
    # Print environment information for debugging
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    
    return logger
