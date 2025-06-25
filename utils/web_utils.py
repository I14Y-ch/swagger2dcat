import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse, urljoin

def detect_language_from_url(url):
    """
    Detect language code from URL
    
    Returns:
        str: Detected language code or None
    """
    # Common language codes
    lang_patterns = [
        r'/([a-z]{2})/', # matches /de/, /fr/, etc.
        r'/([a-z]{2})$', # matches /de, /fr at the end
        r'/([a-z]{2})-[A-Z]{2}/', # matches /en-US/, etc.
        r'\.([a-z]{2})\.' # matches .de., .fr., etc.
    ]
    
    for pattern in lang_patterns:
        matches = re.search(pattern, url.lower())
        if matches:
            lang_code = matches.group(1)
            if lang_code in ['de', 'fr', 'it', 'en']:
                return lang_code
    
    # Default to English if no language detected
    return None

def generate_language_variants(url, original_lang=None):
    """
    Generate URLs for different languages by replacing language codes
    
    Args:
        url (str): Original URL
        original_lang (str): Detected language code in the original URL
    
    Returns:
        dict: URLs for each language {'en': url_en, 'de': url_de, ...}
    """
    if not original_lang:
        original_lang = detect_language_from_url(url) or 'en'
    
    # List of target languages
    target_langs = ['en', 'de', 'fr', 'it']
    
    # Patterns to replace in URL
    patterns = [
        f'/{original_lang}/', # /de/
        f'/{original_lang}-[A-Z]{{2}}/', # /en-US/
        f'/{original_lang}$', # /de at end
        f'.{original_lang}.' # .de.
    ]
    
    url_variants = {original_lang: url}
    
    # Generate URLs for each target language
    for lang in target_langs:
        if lang == original_lang:
            continue
        
        variant_url = url
        for pattern in patterns:
            # Try different replacement patterns
            if re.search(pattern, url, re.IGNORECASE):
                if pattern == f'/{original_lang}/':
                    variant_url = re.sub(f'/{original_lang}/', f'/{lang}/', url, flags=re.IGNORECASE)
                elif pattern == f'/{original_lang}-[A-Z]{{2}}/':
                    variant_url = re.sub(f'/{original_lang}-[A-Z]{{2}}/', f'/{lang}/', url, flags=re.IGNORECASE)
                elif pattern == f'/{original_lang}$':
                    variant_url = re.sub(f'/{original_lang}$', f'/{lang}', url, flags=re.IGNORECASE)
                elif pattern == f'.{original_lang}.':
                    variant_url = re.sub(f'.{original_lang}.', f'.{lang}.', url, flags=re.IGNORECASE)
                
                url_variants[lang] = variant_url
                break
        
        # If no pattern matched, just store the original URL
        if lang not in url_variants:
            url_variants[lang] = url
    
    return url_variants

def extract_doc_links_from_soup(soup, url, doc_extensions):
    """
    Extract document links from BeautifulSoup object
    
    Returns:
        list: List of document link dictionaries
    """
    document_links = []
    
    # Look for links in document sections first
    doc_sections = soup.select('.documents, #documents, .downloads, #downloads, .dokumente, #dokumente')
    if doc_sections:
        for section in doc_sections:
            links = section.find_all('a', href=True)
            for link in links:
                href = link['href']
                if any(href.lower().endswith(ext) for ext in doc_extensions):
                    # Get the link text or alt text from an image if present
                    link_text = link.get_text(strip=True)
                    if not link_text and link.find('img'):
                        link_text = link.find('img').get('alt', '')
                    if not link_text:
                        link_text = href.split('/')[-1]  # Use filename as fallback
                    
                    # Make sure the URL is absolute
                    if not href.startswith(('http://', 'https://')):
                        # Handle relative URLs
                        base_url = '/'.join(url.split('/')[:3])  # http(s)://domain.com
                        if href.startswith('/'):
                            href = base_url + href
                        else:
                            path_url = '/'.join(url.split('/')[:-1]) + '/'
                            href = path_url + href
                    
                    document_links.append({
                        'href': href,
                        'label': link_text,
                        'type': href.split('.')[-1].lower()
                    })
    
    # If no document sections, look for document links throughout the page
    if not document_links:
        for link in soup.find_all('a', href=True):
            href = link['href']
            if any(href.lower().endswith(ext) for ext in doc_extensions):
                # Process the link as above
                link_text = link.get_text(strip=True)
                if not link_text and link.find('img'):
                    link_text = link.find('img').get('alt', '')
                if not link_text:
                    link_text = href.split('/')[-1]
                
                # Make sure the URL is absolute
                if not href.startswith(('http://', 'https://')):
                    base_url = '/'.join(url.split('/')[:3])
                    if href.startswith('/'):
                        href = base_url + href
                    else:
                        path_url = '/'.join(url.split('/')[:-1]) + '/'
                        href = path_url + href
                
                document_links.append({
                    'href': href,
                    'label': link_text,
                    'type': href.split('.')[-1].lower()
                })
    
    return document_links

def extract_web_content(url):
    """
    Extract content from a web page including document links in multiple languages
    
    Returns:
        tuple: (title, meta_description, content, multilingual_document_links)
    """
    try:
        # Detect language from URL
        detected_lang = detect_language_from_url(url)
        
        # Generate language variant URLs
        language_urls = generate_language_variants(url, detected_lang)
        
        # Initialize document links dict by language
        multilingual_doc_links = {
            'en': [], 'de': [], 'fr': [], 'it': []
        }
        
        # Store all documents in language-agnostic format too
        all_document_links = []
        
        # Document extensions to look for
        doc_extensions = ['.pdf', '.doc', '.docx', '.odt', '.xls', '.xlsx', '.ppt', '.pptx']
        
        # First fetch the main URL content
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        # Parse the HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract the title
        title = ""
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.text.strip()
        
        # Extract meta description
        meta_description = ""
        meta_desc_tag = soup.find('meta', attrs={'name': 'description'})
        if meta_desc_tag:
            meta_description = meta_desc_tag.get('content', '')
        
        # Extract main content - first look for common content containers
        content = ""
        content_divs = soup.select('main, article, .content, #content, .main-content, #main-content')
        
        if content_divs:
            # Use the first content div found
            main_content = content_divs[0]
            
            # Extract all paragraph text
            paragraphs = main_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
            content = '\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        else:
            # Fallback to extracting all paragraph text
            paragraphs = soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
            content = '\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        
        # Extract document links from main URL
        main_doc_links = extract_doc_links_from_soup(soup, url, doc_extensions)
        
        # Add to appropriate language and all documents
        current_lang = detected_lang or 'en'
        multilingual_doc_links[current_lang] = main_doc_links
        all_document_links.extend(main_doc_links)
        
        # Try to fetch alternate language pages
        for lang, lang_url in language_urls.items():
            if lang == current_lang or lang_url == url:
                continue  # Skip the original URL we already processed
            
            try:
                # Fetch with short timeout to avoid long delays
                lang_response = requests.get(lang_url, timeout=5)
                if lang_response.status_code == 200:
                    lang_soup = BeautifulSoup(lang_response.text, 'html.parser')
                    lang_doc_links = extract_doc_links_from_soup(lang_soup, lang_url, doc_extensions)
                    
                    # Add to language-specific collection
                    multilingual_doc_links[lang] = lang_doc_links
                    
                    # Add new unique documents to all_document_links
                    # Use URL as key to avoid duplicates across languages
                    existing_urls = [doc['href'] for doc in all_document_links]
                    for doc in lang_doc_links:
                        if doc['href'] not in existing_urls:
                            all_document_links.append(doc)
                    
            except Exception:
                # Continue on error - we'll still have the main language docs
                continue
        
        # Enhance document links with language information
        for doc in all_document_links:
            doc_lang = detect_language_from_url(doc['href'])
            if doc_lang:
                doc['lang'] = doc_lang
            else:
                doc['lang'] = current_lang  # Default to page language
        
        # Extract address information
        address_data = {}
        address_tag = soup.find("address")
        if address_tag:
            # Extract fields using itemprop attributes
            name = address_tag.find(attrs={"itemprop": "name"})
            street = address_tag.find(attrs={"itemprop": "street-address"})
            postal = address_tag.find(attrs={"itemprop": "postal-code"})
            city = address_tag.find(attrs={"itemprop": "locality"})
            section = address_tag.find("span")
            address_data = {
                "name": name.get_text(strip=True) if name else "",
                "section": section.get_text(strip=True) if section else "",
                "street": street.get_text(strip=True) if street else "",
                "postal_code": postal.get_text(strip=True) if postal else "",
                "city": city.get_text(strip=True) if city else ""
            }
        
        # Return the content, the multilingual document links, and address data
        return title, meta_description, content, all_document_links, address_data
        
    except Exception as e:
        return "", "", "", [], {}