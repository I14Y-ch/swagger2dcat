import uuid
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
    # Default contact point structure - removed fn as it's not used in the DCAT schema
    default_contact = {
        "emailInternet": "",
        "org": {
            "de": "Unbekannte Organisation",
            "en": "Unknown Organization",
            "fr": "Organisation inconnue",
            "it": "Organizzazione sconosciuta"
        },
        "adrWork": {
            "de": "",
            "en": "",
            "fr": "",
            "it": ""
        },
        "note": {
            "de": "Für weitere Informationen kontaktieren Sie uns.",
            "en": "For more information, contact us.",
            "fr": "Pour plus d'informations, contactez-nous.",
            "it": "Per ulteriori informazioni, contattaci."
        },
        "telWorkVoice": ""
    }
    
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
                default_contact["org"][lang] = selected_agent['name'][lang]
    else:
        # Fallback to display name for all languages
        display_name = selected_agent.get('display_name', 'Unknown')
        default_contact["org"] = {
            "de": display_name,
            "en": display_name,
            "fr": display_name,
            "it": display_name
        }
    
    # Get address information if available
    address_info = selected_agent.get('address')
    if address_info:
        # Set email if available
        if address_info.get('email'):
            default_contact["emailInternet"] = address_info['email']
        
        # Set phone if available
        if address_info.get('phone'):
            default_contact["telWorkVoice"] = address_info['phone']
        
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
            if org_name and org_name != default_contact["org"]["de"]:
                address_parts.append(org_name)
        
        # Create address string
        if address_parts:
            address_str = ", ".join(address_parts)
            default_contact["adrWork"] = {
                "de": address_str,
                "en": address_str,
                "fr": address_str,
                "it": address_str
            }
        default_contact["fn"] = {
            "de": "test"
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
    # Generate a UUID for the dataset
    dataset_id = str(uuid.uuid4())
    
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
        "id": dataset_id,
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
                "de": translations.get('de', {}).get('keywords', [])[i] if i < len(translations.get('de', {}).get('keywords', [])) else '',
                "en": translations.get('en', {}).get('keywords', [])[i] if i < len(translations.get('en', {}).get('keywords', [])) else '',
                "fr": translations.get('fr', {}).get('keywords', [])[i] if i < len(translations.get('fr', {}).get('keywords', [])) else '',
                "it": translations.get('it', {}).get('keywords', [])[i] if i < len(translations.get('it', {}).get('keywords', [])) else ''
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
            "id": agency_id,
            "name": publisher_name
        },
        "contactPoints": [contact_point],
        "themeCodes": theme_codes if theme_codes else [],
        "accessRightCode": access_rights_code,
        "endpointUrls": [
            {
                "href": swagger_url,
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
                "href": swagger_url,
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
                "href": swagger_url,
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
                "href": "https://swagger.io/specification/",
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
                "href": landing_page_url,
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
            "href": landing_page_url,
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
                "href": doc['href'],
                "label": multi_label(
                    label_text,
                    label_text,
                    label_text,
                    label_text
                )
            })

    return dcat_json