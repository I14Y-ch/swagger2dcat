from datetime import datetime

def get_publisher_name_from_agents(agency_id, agents_list):
    """
    Get publisher name from agents list by agency ID
    """
    if not agents_list:
        return {
            "de": "Unbekannter Herausgeber",
            "en": "Unknown Publisher",
            "fr": "Éditeur inconnu", 
            "it": "Editore sconosciuto",
            "rm": ""
        }
    
    # Check if using the hardcoded "i14y-test-organisation" identifier
    if agency_id == "i14y-test-organisation":
        return {
            "de": "I14Y Test Organisation",
            "en": "I14Y Test Organisation",
            "fr": "I14Y Test Organisation", 
            "it": "I14Y Test Organisation",
            "rm": ""
        }
    
    # Find the agent with matching ID
    for agent in agents_list:
        if agent.get('id') == agency_id:
            display_name = agent.get('display_name', 'Unknown')
            return {
                "de": display_name,
                "en": display_name,
                "fr": display_name,
                "it": display_name,
                "rm": ""
            }
    
    # Fallback if not found
    return {
        "de": "Herausgeber",
        "en": "Publisher",
        "fr": "Éditeur",
        "it": "Editore", 
        "rm": ""
    }

def get_contact_points_from_agent(agency_id, agents_list):
    """
    Get contact points from the selected agency
    """
    # Default contact point structure matching I14Y Partner API VCardModel
    default_contact = {
        "fn": {
            "de": "Unbekannte Organisation",
            "en": "Unknown Organization",
            "fr": "Organisation inconnue",
            "it": "Organizzazione sconosciuta",
            "rm": ""
        },
        "hasAddress": {
            "de": "",
            "en": "",
            "fr": "",
            "it": "",
            "rm": ""
        },
        "hasEmail": "info@example.com",  # Required field for VCardModel
        "hasTelephone": "",
        "kind": "Organization",  # Required field for VCardModel
        "note": {
            "de": "Für weitere Informationen kontaktieren Sie uns.",
            "en": "For more information, contact us.",
            "fr": "Pour plus d'informations, contactez-nous.",
            "it": "Per ulteriori informazioni, contattaci.",
            "rm": ""
        }
    }
    
    # Check if using the hardcoded "i14y-test-organisation" identifier
    if agency_id == "i14y-test-organisation":
        # Special test organization contact details
        test_contact = default_contact.copy()
        test_contact["fn"] = {
            "de": "I14Y Test Organisation",
            "en": "I14Y Test Organisation",
            "fr": "I14Y Test Organisation",
            "it": "I14Y Test Organisation",
            "rm": ""
        }
        test_contact["hasEmail"] = "info@i14y.admin.ch"
        return test_contact
        
    if not agents_list:
        return default_contact
    
    # Find the selected agent
    selected_agent = None
    for agent in agents_list:
        if agent.get('id') == agency_id:
            selected_agent = agent
            break
    
    if not selected_agent:
        return default_contact
    
    # Get the multilingual organization names if available
    if 'name' in selected_agent and isinstance(selected_agent['name'], dict):
        for lang in ['de', 'en', 'fr', 'it']:
            if lang in selected_agent['name'] and selected_agent['name'][lang]:
                default_contact["fn"][lang] = selected_agent['name'][lang]
    else:
        # Fallback to display name for all languages
        display_name = selected_agent.get('display_name', 'Unknown')
        default_contact["fn"] = {
            "de": display_name,
            "en": display_name,
            "fr": display_name,
            "it": display_name,
            "rm": ""
        }
    
    # Get address information if available
    address_info = selected_agent.get('address')
    if address_info:
        # Set email if available
        if address_info.get('email'):
            default_contact["hasEmail"] = address_info['email']
        
        # Set phone if available
        if address_info.get('phone'):
            default_contact["hasTelephone"] = address_info['phone']
        
        # Build address string from department and organization info
        address_parts = []
        
        # Add department name if available
        if address_info.get('department') and isinstance(address_info['department'], dict):
            dept_name = (address_info['department'].get('de') or 
                        address_info['department'].get('en') or 
                        list(address_info['department'].values())[0] if address_info['department'].values() else '')
            if dept_name:
                address_parts.append(dept_name)
        
            # Add organization name if different from display name
            if address_info.get('organization') and isinstance(address_info['organization'], dict):
                org_name = (address_info['organization'].get('de') or 
                       address_info['organization'].get('en') or 
                       list(address_info['organization'].values())[0] if address_info['organization'].values() else '')
                if org_name and org_name != default_contact["fn"]["de"]:
                    address_parts.append(org_name)        # Create address string
        if address_parts:
                address_str = ", ".join(address_parts)
                default_contact["hasAddress"] = {
                    "de": address_str,
                    "en": address_str,
                    "fr": address_str,
                    "it": address_str,
                    "rm": ""
                }
    
    return default_contact

def generate_dcat_json(
    translations, theme_codes, agency_id, swagger_url, landing_page_url=None,
    agents_list=None, access_rights_code="PUBLIC", license_code="", contact_point_override=None,
    document_links=None
):
    """
    Generate DCAT JSON data for I14Y platform with correct structure
    """
    # Note: ID is NOT included - it will be generated by the I14Y platform
    
    # Get publisher name
    publisher_name = get_publisher_name_from_agents(agency_id, agents_list)
    
    # Get contact points from the selected agency or override
    contact_point = contact_point_override if contact_point_override else get_contact_points_from_agent(agency_id, agents_list)

    # Helper for multilingual label
    def multi_label(label_de, label_en, label_fr, label_it):
        return {
            "de": label_de,
            "en": label_en,
            "fr": label_fr,
            "it": label_it
        }

    # Build the JSON structure according to I14Y API requirements
    dcat_json = {
        "title": {
            "de": translations.get('de', {}).get('title', ''),
            "en": translations.get('en', {}).get('title', ''),
            "fr": translations.get('fr', {}).get('title', ''),
            "it": translations.get('it', {}).get('title', '')
        },
        "description": {
            "de": translations.get('de', {}).get('description', ''),
            "en": translations.get('en', {}).get('description', ''),
            "fr": translations.get('fr', {}).get('description', ''),
            "it": translations.get('it', {}).get('description', '')
        },
        "keywords": [
            {
                "label": {
                    "de": translations.get('de', {}).get('keywords', [])[i] if i < len(translations.get('de', {}).get('keywords', [])) else '',
                    "en": translations.get('en', {}).get('keywords', [])[i] if i < len(translations.get('en', {}).get('keywords', [])) else '',
                    "fr": translations.get('fr', {}).get('keywords', [])[i] if i < len(translations.get('fr', {}).get('keywords', [])) else '',
                    "it": translations.get('it', {}).get('keywords', [])[i] if i < len(translations.get('it', {}).get('keywords', [])) else '',
                    "rm": ""
                },
                "uri": None
            }
            for i in range(
                max(
                    len(translations.get('de', {}).get('keywords', [])),
                    len(translations.get('en', {}).get('keywords', [])),
                    len(translations.get('fr', {}).get('keywords', [])),
                    len(translations.get('it', {}).get('keywords', []))
                )
            )
        ],
        "publisher": {
            "identifier": agency_id
        },
        "contactPoints": [{
            # Organization name (fn field as per I14Y spec)
            "fn": {
                "de": (contact_point.get("fn", {}).get("de") or 
                       contact_point.get("org", {}).get("de") or "") if isinstance(contact_point, dict) else "",
                "en": (contact_point.get("fn", {}).get("en") or 
                       contact_point.get("org", {}).get("en") or "") if isinstance(contact_point, dict) else "",
                "fr": (contact_point.get("fn", {}).get("fr") or 
                       contact_point.get("org", {}).get("fr") or "") if isinstance(contact_point, dict) else "",
                "it": (contact_point.get("fn", {}).get("it") or 
                       contact_point.get("org", {}).get("it") or "") if isinstance(contact_point, dict) else "",
                "rm": ""
            },
            
            # Address information (hasAddress field as per I14Y spec)
            "hasAddress": {
                "de": (contact_point.get("hasAddress", {}).get("de") or 
                       contact_point.get("adrWork", {}).get("de") or "") if isinstance(contact_point, dict) else "",
                "en": (contact_point.get("hasAddress", {}).get("en") or 
                       contact_point.get("adrWork", {}).get("en") or "") if isinstance(contact_point, dict) else "",
                "fr": (contact_point.get("hasAddress", {}).get("fr") or 
                       contact_point.get("adrWork", {}).get("fr") or "") if isinstance(contact_point, dict) else "",
                "it": (contact_point.get("hasAddress", {}).get("it") or 
                       contact_point.get("adrWork", {}).get("it") or "") if isinstance(contact_point, dict) else "",
                "rm": ""
            },
            
            # Email is required
            "hasEmail": contact_point.get("hasEmail", "info@example.com") if isinstance(contact_point, dict) else "info@example.com",
            
            # Telephone information (hasTelephone field as per I14Y spec)
            "hasTelephone": (contact_point.get("hasTelephone") or 
                            contact_point.get("telWorkVoice", "")) if isinstance(contact_point, dict) else "",
            
            # Kind must be "Organization" as per I14Y spec
            "kind": "Organization",
            
            # Note information
            "note": {
                "de": contact_point.get("note", {}).get("de", "") if isinstance(contact_point, dict) else "",
                "en": contact_point.get("note", {}).get("en", "") if isinstance(contact_point, dict) else "",
                "fr": contact_point.get("note", {}).get("fr", "") if isinstance(contact_point, dict) else "",
                "it": contact_point.get("note", {}).get("it", "") if isinstance(contact_point, dict) else "",
                "rm": ""
            }
        }],
        "themeCodes": theme_codes if theme_codes else [],
        "accessRights": {
            "code": access_rights_code
        },
        "endpointUrls": [
            {
                "uri": swagger_url,
                "label": multi_label(
                    "API Endpunkt",
                    "API Endpoint",
                    "Point de terminaison API",
                    "Endpoint API"
                )
            }
        ],
        "endpointDescriptions": [
            {
                "uri": swagger_url,
                "label": multi_label(
                    "API-Beschreibung (Swagger/OpenAPI)",
                    "API Description (Swagger/OpenAPI)",
                    "Description de l'API (Swagger/OpenAPI)",
                    "Descrizione dell'API (Swagger/OpenAPI)"
                )
            }
        ],
        "documents": [
            {
                "uri": swagger_url,
                "label": {
                    "de": translations.get('de', {}).get('title', '') or "API-Dokumentation (Swagger/OpenAPI)",
                    "en": translations.get('en', {}).get('title', '') or "API Documentation (Swagger/OpenAPI)",
                    "fr": translations.get('fr', {}).get('title', '') or "Documentation de l'API (Swagger/OpenAPI)",
                    "it": translations.get('it', {}).get('title', '') or "Documentazione API (Swagger/OpenAPI)"
                }
            }
        ],
        "conformTos": [
            {
                "uri": "https://swagger.io/specification/",
                "label": multi_label(
                    "Konform mit OpenAPI (Swagger) Spezifikation",
                    "Conforms to OpenAPI (Swagger) specification",
                    "Conforme à la spécification OpenAPI (Swagger)",
                    "Conforme alle specifiche OpenAPI (Swagger)"
                )
            }
        ],
        "version": datetime.now().strftime("%Y-%m-%d"),
        "versionNotes": {
        }
    }

    # Add license only if specified
    if license_code:
        # Map license codes to their details - using I14Y valid licenses
        license_details = {
            'terms_open': {
                'name': {
                    'de': 'Opendata OPEN: Freie Nutzung.',
                    'en': 'Opendata OPEN: Open use.',
                    'fr': 'Opendata OPEN: Utilisation libre.',
                    'it': 'Opendata OPEN: Libero utilizzo.'
                },
                'uri': 'http://dcat-ap.ch/vocabulary/licenses/terms_open'
            },
            'terms_by': {
                'name': {
                    'de': 'Opendata BY: Freie Nutzung. Quellenangabe ist Pflicht.',
                    'en': 'Opendata BY: Open use. Must provide the source.',
                    'fr': 'Opendata BY: Utilisation libre. Obligation d\'indiquer la source.',
                    'it': 'Opendata BY: Libero utilizzo. Indicazione della fonte obbligatoria.'
                },
                'uri': 'http://dcat-ap.ch/vocabulary/licenses/terms_by'
            },
            'terms_ask': {
                'name': {
                    'de': 'Opendata ASK: Freie Nutzung. Kommerzielle Nutzung nur mit Bewilligung des Datenlieferanten zulässig.',
                    'en': 'Opendata ASK: Open use. Use for commercial purposes requires permission of the data owner.',
                    'fr': 'Opendata ASK: Utilisation libre. Utilisation à des fins commerciales uniquement avec l\'autorisation du fournisseur des données.',
                    'it': 'Opendata ASK: Libero utilizzo. Utilizzo a fini commerciali ammesso soltanto previo consenso del titolare dei dati.'
                },
                'uri': 'http://dcat-ap.ch/vocabulary/licenses/terms_ask'
            },
            'terms_by_ask': {
                'name': {
                    'de': 'Opendata BY ASK: Freie Nutzung. Quellenangabe ist Pflicht. Kommerzielle Nutzung nur mit Bewilligung des Datenlieferanten zulässig.',
                    'en': 'Opendata BY ASK: Open use. Must provide the source. Use for commercial purposes requires permission of the data owner.',
                    'fr': 'Opendata BY ASK: Utilisation libre. Obligation d\'indiquer la source. Utilisation commerciale uniquement avec l\'autorisation du fournisseur des donnés.',
                    'it': 'Opendata BY ASK: Libero utilizzo. Indicazione della fonte obbligatoria. Utilizzo a fini commerciali ammesso soltanto previo consenso del titolare dei dati.'
                },
                'uri': 'http://dcat-ap.ch/vocabulary/licenses/terms_by_ask'
            }
        }
        
        if license_code in license_details:
            dcat_json["license"] = {
                "code": license_code,
                "name": license_details[license_code]['name'],
                "uri": license_details[license_code]['uri']
            }
        else:
            # For unknown license codes (fallback - shouldn't happen with dropdown)
            dcat_json["license"] = {
                "code": license_code,
                "name": {
                    "de": f"Lizenz: {license_code}",
                    "en": f"License: {license_code}",
                    "fr": f"Licence: {license_code}",
                    "it": f"Licenza: {license_code}"
                },
                "uri": ""
            }

    # Add landing pages only if URL is provided
    if landing_page_url:
        dcat_json["landingPages"] = [
            {
                "uri": landing_page_url,
                "label": multi_label(
                    "Weitere Informationen",
                    "More information",
                    "Plus d'informations", 
                    "Maggiori informazioni"
                )
            }
        ]
        # Also add landing page to documents with all translations
        dcat_json["documents"].append({
            "uri": landing_page_url,
            "label": multi_label(
                "Weitere Informationen",
                "More information",
                "Plus d'informations", 
                "Maggiori informazioni"
            )
        })

    # Add document links if provided, with all translations for label
    if document_links:
        for doc in document_links:
            doc_type = doc['type'].upper() if doc['type'] else 'DOC'
            label_text = doc['label'] or f"Document ({doc_type})"
            dcat_json["documents"].append({
                "uri": doc['href'],
                "label": multi_label(
                    label_text,
                    label_text,
                    label_text,
                    label_text
                )
            })

    return dcat_json