import os
import deepl

# Try to import config, if it exists
try:
    from config import DEEPL_API_KEY
except ImportError:
    DEEPL_API_KEY = os.getenv('DEEPL_API_KEY')

print(f"[DEBUG] DEEPL_API_KEY: {DEEPL_API_KEY}")

def translate_from_english(title_en, description_en, keywords_en):
    """
    Translate content from English to all other languages
    """
    # Initialize DeepL client
    try:
        translator = deepl.Translator(DEEPL_API_KEY)
    except Exception as e:
        print(f"[ERROR] Failed to initialize DeepL Translator: {e}")
        return {
            'en': {'title': title_en, 'description': description_en, 'keywords': keywords_en},
            'de': {'title': '', 'description': '', 'keywords': []},
            'fr': {'title': '', 'description': '', 'keywords': []},
            'it': {'title': '', 'description': '', 'keywords': []},
            'rm': {'title': '', 'description': '', 'keywords': []}
        }
    print(f"[DEBUG] Input to translate_from_english: title_en={title_en}, description_en={description_en}, keywords_en={keywords_en}")
    # Initialize result dictionary
    translations = {
        'en': {
            'title': title_en,
            'description': description_en,
            'keywords': keywords_en
        },
        'de': {'title': '', 'description': '', 'keywords': []},
        'fr': {'title': '', 'description': '', 'keywords': []},
        'it': {'title': '', 'description': '', 'keywords': []},
        'rm': {'title': '', 'description': '', 'keywords': []}
    }
    try:
        # Translate to German
        if title_en:
            translations['de']['title'] = translator.translate_text(
                title_en, source_lang="EN", target_lang="DE").text
        if description_en:
            translations['de']['description'] = translator.translate_text(
                description_en, source_lang="EN", target_lang="DE").text
        if keywords_en:
            translations['de']['keywords'] = [
                translator.translate_text(kw, source_lang="EN", target_lang="DE").text
                for kw in keywords_en
            ]
        # Translate to French
        if title_en:
            translations['fr']['title'] = translator.translate_text(
                title_en, source_lang="EN", target_lang="FR").text
        if description_en:
            translations['fr']['description'] = translator.translate_text(
                description_en, source_lang="EN", target_lang="FR").text
        if keywords_en:
            translations['fr']['keywords'] = [
                translator.translate_text(kw, source_lang="EN", target_lang="FR").text
                for kw in keywords_en
            ]
        # Translate to Italian
        if title_en:
            translations['it']['title'] = translator.translate_text(
                title_en, source_lang="EN", target_lang="IT").text
        if description_en:
            translations['it']['description'] = translator.translate_text(
                description_en, source_lang="EN", target_lang="IT").text
        if keywords_en:
            translations['it']['keywords'] = [
                translator.translate_text(kw, source_lang="EN", target_lang="IT").text
                for kw in keywords_en
            ]
    except Exception as e:
        print(f"[ERROR] DeepL translation failed: {e}")
    print(f"[DEBUG] Output from translate_from_english: {translations}")
    return translations

def translate_content(title_de, description_de, keywords_de, title_en=None, description_en=None, keywords_en=None):
    """
    Translate content using DeepL API
    Handles both German and English content as source
    """
    try:
        translator = deepl.Translator(DEEPL_API_KEY)
    except Exception as e:
        print(f"[ERROR] Failed to initialize DeepL Translator: {e}")
        return {
            'de': {'title': title_de, 'description': description_de, 'keywords': keywords_de},
            'en': {'title': title_en if title_en else '', 'description': description_en if description_en else '', 'keywords': keywords_en if keywords_en else []},
            'fr': {'title': '', 'description': '', 'keywords': []},
            'it': {'title': '', 'description': '', 'keywords': []},
            'rm': {'title': '', 'description': '', 'keywords': []}
        }
    print(f"[DEBUG] Input to translate_content: title_de={title_de}, description_de={description_de}, keywords_de={keywords_de}, title_en={title_en}, description_en={description_en}, keywords_en={keywords_en}")
    # Initialize result dictionary
    translations = {
        'de': {
            'title': title_de,
            'description': description_de,
            'keywords': keywords_de
        },
        'en': {
            'title': title_en if title_en else '',
            'description': description_en if description_en else '',
            'keywords': keywords_en if keywords_en else []
        },
        'fr': {'title': '', 'description': '', 'keywords': []},
        'it': {'title': '', 'description': '', 'keywords': []},
        'rm': {'title': '', 'description': '', 'keywords': []}
    }
    try:
        # If English content is missing, translate from German to English
        if not title_en or not description_en or not keywords_en:
            if title_de:
                translations['en']['title'] = translator.translate_text(
                    title_de, source_lang="DE", target_lang="EN-US").text
            if description_de:
                translations['en']['description'] = translator.translate_text(
                    description_de, source_lang="DE", target_lang="EN-US").text
            if keywords_de:
                translations['en']['keywords'] = [
                    translator.translate_text(kw, source_lang="DE", target_lang="EN-US").text
                    for kw in keywords_de
                ]
        # Translate to French
        source_lang = "EN" if title_en else "DE"
        source_title = title_en if title_en else title_de
        source_desc = description_en if description_en else description_de
        source_kw = keywords_en if keywords_en else keywords_de
        if source_title:
            translations['fr']['title'] = translator.translate_text(
                source_title, source_lang=source_lang, target_lang="FR").text
        if source_desc:
            translations['fr']['description'] = translator.translate_text(
                source_desc, source_lang=source_lang, target_lang="FR").text
        if source_kw:
            translations['fr']['keywords'] = [
                translator.translate_text(kw, source_lang=source_lang, target_lang="FR").text
                for kw in source_kw
            ]
        # Translate to Italian
        if source_title:
            translations['it']['title'] = translator.translate_text(
                source_title, source_lang=source_lang, target_lang="IT").text
        if source_desc:
            translations['it']['description'] = translator.translate_text(
                source_desc, source_lang=source_lang, target_lang="IT").text
        if source_kw:
            translations['it']['keywords'] = [
                translator.translate_text(kw, source_lang=source_lang, target_lang="IT").text
                for kw in source_kw
            ]
    except Exception as e:
        print(f"[ERROR] DeepL translation failed: {e}")
    print(f"[DEBUG] Output from translate_content: {translations}")
    return translations