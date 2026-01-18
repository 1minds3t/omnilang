import time
import subprocess
import shutil
import asyncio
import httpx
import requests
import re
import sys
import logging
logger = logging.getLogger(__name__)
from typing import Tuple
import unicodedata
import langdetect
from langdetect import detect, LangDetectException
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Set
import polib
from googletrans import Translator as GoogleTranslator
from typing import Optional, Dict, List, Tuple
import uuid
import json
from omnilang.ai.TranslatorWrapper import TranslationReviewerEngine, TranslationAISelector, TranslationCandidate, MADLADTranslator, MBartTranslator
from omnilang.ai.FixerEngine import TranslationFixerEngine
from omnilang.ai.RefinerEngine import TranslationRefinerEngine
from omnilang.common_utils import get_project_root
import os
import signal
from omnilang.ai.TranslatorWrapper import (
    TranslationReviewerEngine, TranslationAISelector, TranslationCandidate,
    MADLADTranslator, MBartTranslator,
    WingBabelTranslator  # <-- ADD THESE NEW ONES
)

# --- Add the i18n_tools directory to the path to import our modules ---
# This is the only sys.path modification needed.
TOOLS_DIR = Path(__file__).parent.resolve()
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# --- Import from your local tool files ---
# REMOVE THIS LINE - it's causing the circular import:
# from omnilang.core.helper import TranslationHelper, main as translation_helper_main

from omnilang.core.converter import ASTFStringConverter
from omnilang.core import extractor as extract_strings
from omnilang.core import underscore_fixer # Import the newly created module

# --- CORRECTED Resource Management ---
# These functions now correctly call your shell script and do nothing else.
def prepare_for_ai_task():
    """Call the master shell script to prepare for an AI task."""
    master_script = "/home/minds3t/stealth/master-resource-manager.sh"
    print("--- Preparing GPU for AI Task (stopping mining)... ---")
    try:
        subprocess.run([master_script, "prepare-for-ai"], check=True, capture_output=True, text=True)
        print("--- GPU is ready for AI task. ---")
    except subprocess.CalledProcessError as e:
        print(f"--- ERROR: The 'prepare-for-ai' script failed: {e.stderr.strip()} ---")
    except FileNotFoundError:
        print(f"--- ERROR: Master script not found at {master_script} ---")

def resume_mining_operations():
    """Call the master shell script to resume mining."""
    master_script = "/home/minds3t/stealth/master-resource-manager.sh"
    print("--- Signaling AI task completion (resuming mining)... ---")
    try:
        subprocess.run([master_script, "resume-mining"], check=True, capture_output=True, text=True)
        print("--- Mining operations will resume. ---")
    except subprocess.CalledProcessError as e:
        print(f"--- ERROR: The 'resume-mining' script failed: {e.stderr.strip()} ---")
    except FileNotFoundError:
        print(f"--- ERROR: Master script not found at {master_script} ---")

class TranslationHelper:
    def __init__(self, ai_reviewer=None):
        self.ai_reviewer = ai_reviewer 
        self.mbart_translator = None 
        self.madlad_translator = None
        self.wing_babel_translator = None
        
        # Load the configured project root instead of using cwd()
        config_home = os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')
        config_path = Path(config_home) / 'omnilang' / 'i18n_config.json'
        
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    root = Path(config.get('project_root', get_project_root()))
            except:
                root = get_project_root()
        else:
            root = get_project_root()
        
        # Prioritize 'src/omnipkg/locale' (your structure), then others
        candidates = [
            root / 'src' / root.name / 'locale',
            root / 'src' / 'omnipkg' / 'locale',
            root / 'omnipkg' / 'locale',
            root / 'locale',
        ]
        
        # Dynamically find any src/*/locale
        candidates.extend(root.glob('src/*/locale'))
        
        self.locale_dir = root / 'locale'  # Default fallback
        
        for path in candidates:
            if path.exists() and path.is_dir():
                # Verify it contains language folders
                has_langs = any(p.is_dir() and not p.name.startswith('.') for p in path.iterdir())
                if has_langs:
                    self.locale_dir = path
                    break
        
        # Language mapping
        self.api_lang_map = {
            'zh_CN': 'zh', 'zh_TW': 'zh', 'ar_EG': 'ar', 'pt_BR': 'pt',
            'es_ES': 'es', 'fr_FR': 'fr', 'de_DE': 'de', 'it_IT': 'it',
            'ja_JP': 'ja', 'ko_KR': 'ko', 'ru_RU': 'ru', 'hi_IN': 'hi'
        }
        
        # Backup API endpoints
        self.translation_endpoints = []
        
        # DeepL API configuration
        self.deepl_api_key = os.environ.get('DEEPL_API_KEY', '')
        if not self.deepl_api_key:
            logger.warning("DEEPL_API_KEY environment variable not set. DeepL translations will not be available.")

        
        # Corrected class instantiation
        self.google_translator = GoogleTranslator()
        self.google_translator.raise_Exception = True
        
        # Discover languages AFTER setting the correct directory
        self.languages = self._discover_languages()

    def rescan_and_fix(self, target_lang: Optional[str] = None):
        """
        Scans existing translations for quality issues and uses the AI to fix them.
        """
        # --- THIS IS THE FIX ---
        # Wrap the whole process in the resource manager calls
        prepare_for_ai_task()
        try:
            if target_lang:
                if target_lang in self.languages:
                    self._rescan_and_fix_language(target_lang)
                else:
                    print(f"❌ Error: Language '{target_lang}' not found.")
            else:
                languages_to_process = [lang for lang in self.languages.keys() if lang != 'en']
                for i, lang_code in enumerate(languages_to_process):
                    print("\n" + "="*60)
                    print(f"🔬 Rescanning language {i+1}/{len(languages_to_process)}: {self.languages.get(lang_code, lang_code)}")
                    self._rescan_and_fix_language(lang_code)
        finally:
            resume_mining_operations()

    def _rescan_and_fix_language(self, lang_code: str):
        """
        The core logic for rescanning a single language file.
        """
        po_file = self.get_po_file(lang_code)
        if not po_file: return

        # Find ALL entries that are already translated but are suspicious
        # This is the key difference: we check entry.msgstr to find existing translations
        suspicious_entries = [
            entry for entry in po_file 
            if entry.msgid and entry.msgstr and self._is_translation_suspicious(entry, lang_code)[0]
        ]

        if not suspicious_entries:
            print(f"✅ No quality issues found for {self.languages.get(lang_code, lang_code)}!")
            return
        
        print(f"🚨 Found {len(suspicious_entries)} existing translations with quality issues. Sending to AI for refinement...")

        # Get the destination language for the API
        api_dest_lang = self.api_lang_map.get(lang_code, lang_code.split('_')[0])
        
        # We can reuse the exact same AI batch process!
        try:
            ai_reviewer = TranslationReviewerEngine()
            # We run the AI process and wait for it to complete
            asyncio.run(self._batch_translate_async_ai(suspicious_entries, api_dest_lang, po_file, ai_reviewer))
        except FileNotFoundError as e:
            print(f"❌ AI Engine setup failed: {e}")
        except Exception as e:
            print(f"❌ An unexpected error occurred during the AI batch process: {e}")

    async def _translate_google_fallback(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Google Translate fallback with ROBUST placeholder protection."""
        try:
            placeholder_pattern = r'\{[^{}]*\}'
            placeholders = re.findall(placeholder_pattern, text)
            
            if not placeholders:
                # CORRECTED: Await the translate coroutine directly
                translation = await asyncio.to_thread(self.google_translator.translate,
                    text,
                    dest=dest_lang,
                    src=src_lang
                )
                return translation.text if translation and translation.text else None
            
            protected_text = text
            placeholder_map = {}
            for i, placeholder in enumerate(placeholders):
                protected_token = f'<PLACEHOLDER{i:03d}PLACEHOLDER>'
                protected_text = protected_text.replace(placeholder, protected_token, 1)
                placeholder_map[protected_token] = placeholder
            
            print(f"Protected text: {protected_text}")
            
            # CORRECTED: Await the translate coroutine directly
            translation = await asyncio.to_thread(self.google_translator.translate,
                protected_text,
                dest=dest_lang,
                src=src_lang
            )
            
            if not translation or not translation.text:
                return None
            translated_text = translation.text
            print(f"Raw translation: {translated_text}")
            
            for protected_token, original_placeholder in placeholder_map.items():
                if protected_token in translated_text:
                    translated_text = translated_text.replace(protected_token, original_placeholder)
                else:
                    pattern_base = protected_token.replace('<', '').replace('>', '').replace('PLACEHOLDER', '')
                    corruption_patterns = [
                        rf'<\s*PLACEHOLDER\s*{re.escape(pattern_base)}\s*PLACEHOLDER\s*>',
                        rf'&lt;\s*PLACEHOLDER\s*{re.escape(pattern_base)}\s*PLACEHOLDER\s*&gt;',
                        rf'PLACEHOLDER\s*{re.escape(pattern_base)}\s*PLACEHOLDER',
                        rf'<.*?{re.escape(pattern_base)}.*?>',
                    ]
                    restored = False
                    for pattern in corruption_patterns:
                        if re.search(pattern, translated_text, re.IGNORECASE):
                            translated_text = re.sub(pattern, original_placeholder, translated_text, count=1, flags=re.IGNORECASE)
                            restored = True
                            break
                    if not restored:
                        print(f"WARNING: Could not restore placeholder {protected_token} -> {original_placeholder}")
            
            print(f"Final translation: {translated_text}")
            
            is_valid, reason = self._validate_translation(text, translated_text)
            if is_valid:
                return translated_text 
            else:
                    print(f"\n    - 👎 REJECTED (Reason: {reason})")
                    print(f"    - Bad Translation: '{translated_text}'\n")
                    return None

        except Exception as e:
            print(f"Base64 translation error: {e}")
            return None

    async def _translate_google_fallback_v2(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Alternative approach with sentence-by-sentence translation."""
        try:
            placeholder_pattern = r'(\{[^{}]*\})'
            segments = re.split(placeholder_pattern, text)
            translated_segments = []
            
            for segment in segments:
                if re.match(r'\{[^{}]*\}', segment):
                    translated_segments.append(segment)
                elif segment.strip():
                    # CORRECTED: Await the translate coroutine directly
                    translation = await asyncio.to_thread(self.google_translator.translate,
                        segment,
                        dest=dest_lang,
                        src=src_lang
                    )
                    if translation and translation.text:
                        translated_segments.append(translation.text)
                    else:
                        translated_segments.append(segment)
                else:
                    translated_segments.append(segment)
            
            result = ''.join(translated_segments)
            is_valid, reason = self._validate_translation(text, result)
            return result if is_valid else None
                
        except Exception as e:
            print(f"Google Translate error: {e}")
            return None


        # Third approach: Use HTML entities
        # Third approach: Use HTML entities
    async def _translate_google_fallback_v3(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Use HTML entities to protect placeholders."""
        try:
            import html
            
            # 1. Replace placeholders with HTML entities
            placeholder_pattern = r'\{[^{}]*\}'
            placeholders = re.findall(placeholder_pattern, text)
            
            if not placeholders:
                # CORRECTED: Await the translate coroutine directly
                translation = await asyncio.to_thread(self.google_translator.translate,
                    text,
                    dest=dest_lang,
                    src=src_lang
                )
                return translation.text if translation and translation.text else None
            
            # Encode placeholders as HTML entities
            protected_text = text
            placeholder_map = {}
            
            for i, placeholder in enumerate(placeholders):
                # Convert to HTML entities
                encoded = html.escape(placeholder)
                # Further protect with special markers
                protected_token = f"__{encoded}__"
                protected_text = protected_text.replace(placeholder, protected_token, 1)
                placeholder_map[protected_token] = placeholder
            
            # 2. Translate - CORRECTED: Await the translate coroutine directly
            translation = await asyncio.to_thread(self.google_translator.translate,
                protected_text,
                dest=dest_lang,
                src=src_lang
            )
            
            if not translation or not translation.text:
                return None
                
            translated_text = translation.text
            
            # 3. Restore placeholders
            for protected_token, original_placeholder in placeholder_map.items():
                translated_text = translated_text.replace(protected_token, original_placeholder)
            
            # 4. Validate
            is_valid, reason = self._validate_translation(text, translated_text)
            if is_valid:
                return translated_text
            else:
                print(f"\n    - 👎 REJECTED (Reason: {reason})")
                print(f"    - Bad Translation: '{translated_text}'\n")
                return None
                
        except Exception as e:
            print(f"Google Translate error: {e}")
            return None

    async def _translate_google_fallback_v4(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Bulletproof Google Translate with untranslatable placeholder protection."""
        try:
            # 1. EXTRACT PLACEHOLDERS
            placeholder_pattern = r'\{[^{}]*\}'
            placeholders = re.findall(placeholder_pattern, text)
            
            if not placeholders:
                # No placeholders, translate directly - CORRECTED: Await the translate coroutine directly
                translation = await asyncio.to_thread(self.google_translator.translate,
                    text,
                    dest=dest_lang,
                    src=src_lang
                )
                return translation.text if translation and translation.text else None
            
            # 2. USE NUMERIC-ONLY TOKENS (Google won't translate numbers)
            protected_text = text
            placeholder_map = {}
            
            # Use pure numbers as protection - Google rarely translates pure numerics
            for i, placeholder in enumerate(placeholders):
                # Create a unique numeric token that's unlikely to be translated
                protected_token = f"99999{i:04d}88888"  # e.g., 9999900018888
                protected_text = protected_text.replace(placeholder, protected_token, 1)
                placeholder_map[protected_token] = placeholder
            
            print(f"Protected text: {protected_text}")
            print(f"Placeholder map: {placeholder_map}")
            
            # 3. TRANSLATE - CORRECTED: Await the translate coroutine directly
            translation = await asyncio.to_thread(self.google_translator.translate,
                protected_text,
                dest=dest_lang,
                src=src_lang
            )
            
            if not translation or not translation.text:
                return None
                
            translated_text = translation.text
            print(f"Raw translation: {translated_text}")
            
            # 4. RESTORE with robust pattern matching
            for protected_token, original_placeholder in placeholder_map.items():
                # Try exact match first
                if protected_token in translated_text:
                    translated_text = translated_text.replace(protected_token, original_placeholder)
                else:
                    # Handle spacing/formatting corruption
                    # Look for the numeric pattern with possible spaces/punctuation
                    base_pattern = protected_token[:5] + r'[\s\u200B]*' + protected_token[5:9] + r'[\s\u200B]*' + protected_token[9:]
                    
                    def replacer(match):
                        return original_placeholder
                    
                    # Replace any corrupted version
                    translated_text = re.sub(base_pattern, replacer, translated_text)
            
            print(f"Final translation: {translated_text}")
            
            # 5. VALIDATE
            is_valid, reason = self._validate_translation(text, translated_text)
            if is_valid:
                return translated_text
            else:
                print(f"\n    - 👎 REJECTED (Reason: {reason})")
                print(f"    - Bad Translation: '{translated_text}'\n")
                return None
                
        except Exception as e:
            print(f"Google Translate error: {e}")
            return None

    async def _translate_google_fallback_v5(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """
        Split text around placeholders and translate segments separately.
        This version is corrected to properly handle async translation with retry logic.
        """
        try:
            # Split text while preserving the placeholders as separate items in the list
            placeholder_pattern = r'(\{[^{}]*\})'
            segments = re.split(placeholder_pattern, text)
            
            translated_segments = []
            
            for segment in segments:
                # Case 1: The segment is a placeholder (e.g., '{key}')
                if re.fullmatch(placeholder_pattern, segment):
                    translated_segments.append(segment)
                    continue
                
                # Case 2: The segment is actual text that needs translation
                if segment.strip():
                    # --- RETRY LOGIC ---
                    max_retries = 3
                    backoff_time = 2  # Start with a 2-second wait
                    for attempt in range(max_retries):
                        try:
                            translation = await asyncio.to_thread(
                                self.google_translator.translate,
                                segment, dest=dest_lang, src=src_lang
                            )
                            
                            if translation and hasattr(translation, 'text') and translation.text:
                                translated_segments.append(translation.text)
                            else:
                                translated_segments.append(segment)
                            
                            break # Success, exit the retry loop

                        except Exception as e:
                            # Check if this is a '429' error
                            if '429' in str(e):
                                if attempt < max_retries - 1:
                                    print(f"\n    - Received '429 Too Many Requests'. Waiting {backoff_time}s before retry...")
                                    await asyncio.sleep(backoff_time)
                                    backoff_time *= 2  # Exponential backoff
                                else:
                                    print(f"\n    - Google API error after multiple retries: {e}")
                                    translated_segments.append(segment) # Fallback after final retry
                            else:
                                # It's a different error, don't retry
                                print(f"\n    - Google API error during segment translation: {e}")
                                translated_segments.append(segment)
                                break # Exit loop for other errors
                    # --- END OF RETRY LOGIC ---
                else:
                    # Case 3: The segment is just whitespace between placeholders
                    translated_segments.append(segment)
            
            # Reassemble the final string
            result = ''.join(translated_segments)
            
            # Validate that the reassembled string is still valid
            is_valid, reason = self._validate_translation(text, result)
            if is_valid:
                return result
            else:
                # Show the reason AND the rejected text so you can see what's wrong.
                print(f"\n    - 👎 REJECTED (Reason: {reason})")
                print(f"    - Bad Translation: '{result}'\n")
                return None
                
        except Exception as e:
            print(f" - Unexpected error in _translate_google_fallback_v5: {e}")
            return None

        # Most robust: Use Base64 encoding
    async def _translate_google_fallback_v6(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Use Base64-encoded tokens that Google won't touch."""
        try:
            import base64
            
            placeholder_pattern = r'\{[^{}]*\}'
            placeholders = re.findall(placeholder_pattern, text)
            
            if not placeholders:
                # CORRECTED: Await the translate coroutine directly
                translation = await asyncio.to_thread(self.google_translator.translate,
                    text,
                    dest=dest_lang,
                    src=src_lang
                )
                return translation.text if translation and translation.text else None
            
            # Encode placeholders as Base64
            protected_text = text
            placeholder_map = {}
            
            for i, placeholder in enumerate(placeholders):
                # Create a unique identifier and encode it
                unique_id = f"PH{i:04d}"
                encoded = base64.b64encode(unique_id.encode()).decode().rstrip('=')
                protected_token = f"_{encoded}_"  # Wrap in underscores
                
                protected_text = protected_text.replace(placeholder, protected_token, 1)
                placeholder_map[protected_token] = placeholder
            
            print(f"Protected text: {protected_text}")
            
            # Translate - CORRECTED: Await the translate coroutine directly
            translation = await asyncio.to_thread(self.google_translator.translate,
                protected_text,
                dest=dest_lang,
                src=src_lang
            )
            
            if not translation or not translation.text:
                return None
                
            translated_text = translation.text
            print(f"Raw translation: {translated_text}")
            
            # Restore placeholders
            for protected_token, original_placeholder in placeholder_map.items():
                translated_text = translated_text.replace(protected_token, original_placeholder)
            
            # Validate
            is_valid, reason = self._validate_translation(text, translated_text)
            if is_valid:
                return translated_text
            else:
                print(f"\n    - 👎 REJECTED (Reason: {reason})")
                print(f"    - Bad Translation: '{translated_text}'\n")
                return None
                
        except Exception as e:
            print(f"Base64 translation error: {e}")
            return None

    async def _translate_google_fallback_v7(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Google Translate fallback with UNICODE STEALTH placeholder protection."""
        try:
            # 1. EXTRACT AND PROTECT PLACEHOLDERS USING INVISIBLE UNICODE
            placeholder_pattern = r'\{[^{}]*\}'
            placeholders = re.findall(placeholder_pattern, text)
            
            if not placeholders:
                # No placeholders to protect, translate directly - CORRECTED: Await the translate coroutine directly
                translation = await asyncio.to_thread(self.google_translator.translate,
                    text,
                    dest=dest_lang,
                    src=src_lang
                )
                return translation.text if translation and translation.text else None
            
            # Use ZERO-WIDTH UNICODE characters that are invisible but preserved
            # These are Unicode control characters that won't affect rendering
            protected_text = text
            placeholder_map = {}
            
            # Invisible Unicode markers (Google won't translate these)
            invisible_chars = [
                '\u200B', '\u200C', '\u200D', '\u2060',  # Zero-width spaces
                '\uFEFF', '\u202A', '\u202B', '\u202C', '\u202D', '\u202E'  # Directional markers
            ]
            
            for i, placeholder in enumerate(placeholders):
                # Create an invisible but unique marker
                invisible_token = f'{invisible_chars[i % len(invisible_chars)] * 3}{i:03d}{invisible_chars[(i + 1) % len(invisible_chars)] * 3}'
                protected_text = protected_text.replace(placeholder, invisible_token, 1)
                placeholder_map[invisible_token] = placeholder
            
            print(f"Protected text (invisible markers): {protected_text.encode('unicode_escape')}")
            
            # 2. TRANSLATE the protected text - CORRECTED: Await the translate coroutine directly
            translation = await asyncio.to_thread(self.google_translator.translate,
                protected_text,
                dest=dest_lang,
                src=src_lang
            )
            
            if not translation or not translation.text:
                return None
                
            translated_text = translation.text
            print(f"Raw translation: {translated_text}")
            print(f"Raw translation (escaped): {translated_text.encode('unicode_escape')}")
            
            # 3. RESTORE placeholders from invisible markers
            for invisible_token, original_placeholder in placeholder_map.items():
                if invisible_token in translated_text:
                    translated_text = translated_text.replace(invisible_token, original_placeholder)
                else:
                    # Try to find the invisible pattern in the translated text
                    # Google might modify the invisible characters slightly
                    pattern = re.escape(invisible_token)
                    matches = re.search(pattern, translated_text)
                    if matches:
                        translated_text = re.sub(pattern, original_placeholder, translated_text)
                    else:
                        print(f"WARNING: Could not restore invisible placeholder: {invisible_token.encode('unicode_escape')}")
            
            print(f"Final translation: {translated_text}")
            
            # 4. VALIDATE the result
            is_valid, reason = self._validate_translation(text, translated_text)
            if is_valid:
                return translated_text
            else:
                print(f"\n    - 👎 REJECTED (Reason: {reason})")
                print(f"    - Bad Translation: '{translated_text}'\n")
                return None
                
        except Exception as e:
            print(f"Google Translate error: {e}")
            return None

    async def _translate_google_fallback_v8(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Google Translate fallback with EMOJI placeholder protection."""
        try:
            rare_emojis = [
                '⚡', '⚙️', '⚗️', '⚛️', '⚜️', '⚠️', '⚧️', '⚪', '⚫', '⚬',
                '⛔', '⭕', '✅', '❌', '❎', '➡️', '⬅️', '⬆️', '⬇️', '↔️',
                '↕️', '↖️', '↗️', '↘️', '↙️', '↩️', '↪️', '⌚', '⌛', '⌨️',
                '⏏️', '⏩', '⏪', '⏫', '⏬', '⏭️', '⏮️', '⏯️', '⏰', '⏱️'
            ]
            
            placeholder_pattern = r'\{[^{}]*\}'
            placeholders = re.findall(placeholder_pattern, text)
            
            if not placeholders:
                # CORRECTED: Await the translate coroutine directly
                translation = await asyncio.to_thread(self.google_translator.translate,
                    text, dest=dest_lang, src=src_lang
                )
                return translation.text if translation and translation.text else None

            protected_text = text
            placeholder_map = {}
            
            for i, placeholder in enumerate(placeholders):
                emoji_token = f'{rare_emojis[i % len(rare_emojis)]}{i:02d}{rare_emojis[(i + 1) % len(rare_emojis)]}'
                protected_text = protected_text.replace(placeholder, emoji_token, 1)
                placeholder_map[emoji_token] = placeholder
            
            print(f"Protected text (emoji markers): {protected_text}")
            
            # CORRECTED: Await the translate coroutine directly
            translation = await asyncio.to_thread(self.google_translator.translate,
                protected_text,
                dest=dest_lang,
                src=src_lang
            )
            
            if not translation or not translation.text:
                return None
            translated_text = translation.text
            
            for emoji_token, original_placeholder in placeholder_map.items():
                if emoji_token in translated_text:
                    translated_text = translated_text.replace(emoji_token, original_placeholder)
                else:
                    emoji_pattern = re.escape(emoji_token[:1]) + r'.*?' + re.escape(emoji_token[-1:])
                    if re.search(emoji_pattern, translated_text):
                        translated_text = re.sub(emoji_pattern, original_placeholder, translated_text)
            
            is_valid, reason = self._validate_translation(text, translated_text)
            if is_valid:
                return translated_text 
            else:
                print(f"\n    - 👎 REJECTED (Reason: {reason})")
                print(f"    - Bad Translation: '{translated_text}'\n")
                return None
            
        except Exception as e:
            print(f"Base64 translation error: {e}")
            return None

    # Helper function to check what Google is doing to your tokens
    async def debug_google_translation(self, text: str, dest_lang: str, src_lang: str = 'en'):
        """Debug function to see how Google corrupts your placeholders."""
        test_cases = [
            "__PLACEHOLDER_001__",
            "<PLACEHOLDER001PLACEHOLDER>",
            "{{PLACEHOLDER001}}",
            "&lt;PLACEHOLDER001&gt;",
            "___KEEP_THIS___",
        ]
        
        print("Testing how Google handles different protection tokens:")
        for token in test_cases:
            test_text = f"Hello {token} world"
            try:
                translation = await asyncio.to_thread(
                    self.google_translator.translate,
                    test_text,
                    dest=dest_lang,
                    src=src_lang
                )
                result = translation.text if translation else "FAILED"
                print(f"  {test_text} -> {result}")
            except Exception as e:
                print(f"  {test_text} -> ERROR: {e}")

    async def _get_best_translation_interactively(self, text: str, dest_lang: str) -> Optional[str]:
        """
        Runs an intelligent "translation tournament" and now includes a pre-check
        to automatically skip strings that have no translatable content.
        """
        print(f"  - Original: '{text[:70]}...'")
            # Lazy load the heavy mBART model only when it's first needed
        if self.mbart_translator is None:
            try:
                self.mbart_translator = MBartTranslator()
            except Exception as e:
                print(f"    - ⚠️  Could not load mBART-50 model: {e}")
                self.mbart_translator = None # Ensure it's None on failure

        # --- THIS IS THE NEW PRE-CHECK USING YOUR FUNCTION ---
        if self._should_skip_translation(text):
            print("    - 💡 SKIPPED: String has no translatable content. Using original text.")
            return text # Automatically return the original string
        # --- END OF PRE-CHECK ---

        # Add context to the string if it looks technical
        text_to_translate, context_prefix = self._add_context_for_translation(text)
        if context_prefix:
            print(f"  - CONTEXT ADDED: Sending '{text_to_translate[:80]}...' to API.")

        strategies = []
        if self.mbart_translator:
            strategies.append(("mBART-50", self.mbart_translator.translate))

        strategies.extend([
            ("DeepL", self._translate_deepl),
            ("Google (Split v5)", self._translate_google_fallback_v5),
            ("Google (Base64 v6)", self._translate_google_fallback_v6),
            ("Google (Unicode Stealth v7)", self._translate_google_fallback_v7),
            ("Google (Emoji Protect v8)", self._translate_google_fallback_v8),
            ("Google (Numeric v4)", self._translate_google_fallbackv4),
            ("Google (HTML v3)", self._translate_google_fallback_v3),
            ("Google (Protect v1)", self._translate_google_fallback),
            ("google (Protect v2)", self._translate_google_fallback_v2)
        ])

        valid_translations = []

        for name, method in strategies:
            print(f"    - Trying strategy: {name}...", end='', flush=True)
            await asyncio.sleep(1.5)
            
            translation = await method(text_to_translate, dest_lang)

            if translation:
                if context_prefix:
                    translated_prefix_obj = await asyncio.to_thread(self.google_translator.translate, context_prefix, dest=dest_lang)
                    if translated_prefix_obj and translated_prefix_obj.text:
                        translation = translation.replace(translated_prefix_obj.text, "").strip()

                is_valid, reason = self._validate_translation(text, translation)
                if is_valid:
                    print(" ✅ Valid")
                    valid_translations.append({"name": name, "text": translation})
                else:
                    print(f" ❌ Invalid ({reason}) -> REJECTED: '{translation[:70]}...'")
            else:
                print(" ❌ Failed")

        # --- Interactive Choice Logic (Remains the same) ---
        if not valid_translations:
            print("    - ❌ All translation strategies failed.")
            choice = input("  - Enter manual translation (m), use original (o), or skip (s): ").strip().lower()
            if choice == 'm':
                manual_text = input("  - Enter your corrected translation: ").strip()
                return manual_text if manual_text else None
            elif choice == 'o':
                return text
            else:
                return None

        print("\n  - Reviewing all valid translations. Please choose the best one:")
        for i, trans in enumerate(valid_translations):
            print(f"    {i+1}) [{trans['name']}] {trans['text']}")
        
        print("    -----------------------------------------")
        print("    m) Enter a Manual correction")
        print("    o) Use Original Text (don't translate)")
        print("    s) Skip (keep current or leave empty)")

        while True:
            choice = input("  - Your choice (1, 2, ..., m, o, s): ").strip().lower()
            if choice == 's': return None
            if choice == 'o': return text
            if choice == 'm':
                manual_text = input("  - Enter your corrected translation: ").strip()
                return manual_text if manual_text else None
            try:
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(valid_translations):
                    return valid_translations[choice_idx]["text"]
                else:
                    print("    - Invalid number. Please try again.")
            except ValueError:
                print("    - Invalid input. Please enter a number, 'm', 'o', or 's'.")


        
    # ADD THE MISSING DEEPL TRANSLATION METHOD:
    async def _translate_deepl(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Use DeepL API for reliable translation."""
        try:
            # Map language codes to DeepL format - ADD SUPPORT CHECKS!
            deepl_supported_languages = {
                'zh', 'zh_CN', 'zh_TW', 'bg', 'cs', 'da', 'nl', 'en', 'et',
                'fi', 'fr', 'de', 'el', 'hu', 'id', 'it', 'ja', 'ko', 'lv',
                'lt', 'nb', 'pl', 'pt', 'pt_BR', 'ro', 'ru', 'sk', 'sl',
                'es', 'sv', 'tr', 'uk'
            }
            
            # Check if DeepL supports this language
            if dest_lang not in deepl_supported_languages:
                print(f"    - DeepL does not support language '{dest_lang}', skipping")
                return None
            
            deepl_lang_map = {
                'zh': 'ZH', 'zh_CN': 'ZH', 'zh_TW': 'ZH',
                'pt': 'PT-PT', 'pt_BR': 'PT-BR',
                'es': 'ES', 'fr': 'FR', 'de': 'DE', 'it': 'IT',
                'ja': 'JA', 'ko': 'KO', 'ru': 'RU', 'nl': 'NL',
                'pl': 'PL', 'sv': 'SV', 'da': 'DA', 'fi': 'FI',
                'el': 'EL', 'cs': 'CS', 'ro': 'RO', 'hu': 'HU',
                'sk': 'SK', 'bg': 'BG', 'sl': 'SL', 'lt': 'LT',
                'lv': 'LV', 'et': 'ET', 'mt': 'MT', 'tr': 'TR'
            }
            
            target_lang = deepl_lang_map.get(dest_lang, dest_lang.upper())
            
            response = requests.post(
                "https://api-free.deepl.com/v2/translate",
                data={
                    "auth_key": self.deepl_api_key,
                    "text": text,
                    "source_lang": src_lang.upper(),
                    "target_lang": target_lang,
                    "tag_handling": "xml"
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()["translations"][0]["text"]
            else:
                print(f"DeepL API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"DeepL translation error: {e}")
            return None

    def _discover_languages(self) -> dict:
        langs = {}
        if not self.locale_dir.is_dir():
            return {}
        for po_path in self.locale_dir.glob('**/LC_MESSAGES/*.po'):
            lang_code = po_path.parent.parent.name
            lang_name = lang_code.replace('_', ' ').title()
            langs[lang_code] = lang_name
        return dict(sorted(langs.items()))

    def get_po_file(self, lang_code: str) -> Optional[polib.POFile]:
        po_path = self.locale_dir / lang_code / "LC_MESSAGES" / "omnipkg.po"
        if not po_path.exists():
            print(f" ⚠️  .po file not found for {lang_code} at {po_path}")
            return None
        return polib.pofile(str(po_path))

    def refine_low_quality_translations(self, target_lang: Optional[str] = None, threshold: int = 3):
        """Orchestrates the full quality refinement process."""
        asyncio.run(self._refine_low_quality_async(target_lang, threshold))

    async def _refine_low_quality_async(self, target_lang, threshold):
        quality_auditor = TranslationQualityAuditorEngine()
        ai_refiner = TranslationRefinerEngine()
        ai_chooser = TranslationReviewerEngine()
        json_preparer = TranslationAISelector()

        languages_to_process = [target_lang] if target_lang else [lang for lang in self.languages.keys() if lang != 'en']
        
        for lang_code in languages_to_process:
            print(f"\n--- Refining low-quality translations for {lang_code} ---")
            po_file = self.get_po_file(lang_code)
            if not po_file: continue

            translations_to_audit = { e.msgid: {"original": e.msgid, "translation": e.msgstr} for e in po_file if e.msgid and e.msgstr }
            if not translations_to_audit: continue
            
            print("    - Stage 1: Auditing for linguistic quality...")
            audit_results = quality_auditor.batch_audit_quality(translations_to_audit, lang_code)
            
            strings_to_refine = { mid: res for mid, res in audit_results.items() if res['quality_score'] <= threshold }
            
            if not strings_to_refine:
                print("    - ✅ No low-quality strings found to refine.")
                continue

            print(f"    - Stage 2: Found {len(strings_to_refine)} strings to refine. Beginning refinement tournament...")
            refined_count = 0
            for msgid, result in strings_to_refine.items():
                print(f"    - Refining '{msgid[:50]}...'")
                
                candidates = []
                # Candidate 1: The AI Refiner's attempt
                refinement = ai_refiner.refine_translation(result['original'], result['translation'], lang_code)
                if refinement and 'improved_translation' in refinement and self._validate_translation(msgid, refinement['improved_translation'])[0]:
                    candidates.append(TranslationCandidate(id=len(candidates)+1, strategy="AI Refiner", text=refinement['improved_translation']))
                
                # Candidate 2: The DeepL translation (if available)
                api_dest_lang = self.api_lang_map.get(lang_code, lang_code.split('_')[0])
                deepl_trans = await self._translate_deepl(result['original'], api_dest_lang)
                if deepl_trans and self._validate_translation(msgid, deepl_trans)[0]:
                     candidates.append(TranslationCandidate(id=len(candidates)+1, strategy="DeepL", text=deepl_trans))

                # Candidate 3: The original (low-quality) translation, as a fallback
                candidates.append(TranslationCandidate(id=len(candidates)+1, strategy="Original Low-Quality", text=result['translation']))

                final_choice_text = None
                if len(candidates) > 1:
                    print(f"      - 🧠 Found {len(candidates)} options for final review.")
                    selection_data = json_preparer.prepare_selection_json(msgid, api_dest_lang, candidates)
                    ai_decision = ai_chooser.select_best(selection_data)
                    if ai_decision and 'selected_id' in ai_decision:
                        match = next((c for c in candidates if c.id == ai_decision['selected_id']), None)
                        if match:
                            final_choice_text = match.text
                            print(f"      - 🤖 AI Chose [{match.strategy}] as the winner.")
                elif candidates:
                    final_choice_text = candidates[0].text
                    print(f"      - ✅ Only one valid option found: [{candidates[0].strategy}].")
                
                if final_choice_text:
                    entry_to_update = po_file.find(msgid)
                    if entry_to_update:
                        entry_to_update.msgstr = final_choice_text
                        if 'fuzzy' in entry_to_update.flags: entry_to_update.flags.remove('fuzzy')
                        refined_count += 1
            
            if refined_count > 0:
                print(f"    💾 Refined and saved {refined_count} translations.")
                po_file.save()

    def _validate_translation(self, original: str, translated: str, target_lang: str = None) -> Tuple[bool, str]:
        """Balanced validation for technical Amharic translations, allowing proper noun transliterations."""
        # Check for empty or whitespace-only translations
        if not translated or translated.strip() == '':
            return False, "empty_translation"

        # Normalize Unicode to handle diacritics and combining characters
        original_normalized = unicodedata.normalize('NFKC', original.strip())
        translated_normalized = unicodedata.normalize('NFKC', translated.strip())

        # Extract and protect quoted strings
        quoted_pattern = r'\'[^\']*\'|\"[^\"]*\"'
        original_quoted = re.findall(quoted_pattern, original_normalized)
        translated_quoted = re.findall(quoted_pattern, translated_normalized)
        if original_quoted != translated_quoted:
            return False, "quoted_string_mismatch"

        # Replace quoted strings with placeholders to avoid false positives
        quote_placeholder = "__QUOTED__"
        original_no_quotes = re.sub(quoted_pattern, quote_placeholder, original_normalized)
        translated_no_quotes = re.sub(quoted_pattern, quote_placeholder, translated_normalized)

        # Check if placeholders are preserved EXACTLY
        original_placeholders = re.findall(r'\{[^}]*\}', original_no_quotes)
        translated_placeholders = re.findall(r'\{[^}]*\}', translated_no_quotes)
        if original_placeholders != translated_placeholders:
            return False, "placeholder_mismatch"

        # Check for numbered placeholders (e.g., {0}, {1})
        original_numbered = set(re.findall(r'\{\d+\}', original_no_quotes))
        translated_numbered = set(re.findall(r'\{\d+\}', translated_no_quotes))
        if original_numbered != translated_numbered:
            return False, "numbered_placeholder_mismatch"

        # Skip validation for very short strings (e.g., single characters)
        if len(original_no_quotes.strip()) <= 2:
            return True, "short_string_skipped"

        # Check if translation is identical to original (suspicious unless target is English)
        if original_no_quotes == translated_no_quotes and (target_lang != 'en' if target_lang else True):
            return False, "identical_to_original"

        # Check for reasonable length ratio (lenient for Amharic and technical strings)
        original_text = re.sub(r'\{[^}]*\}', '', original_no_quotes).strip()
        translated_text = re.sub(r'\{[^}]*\}', '', translated_no_quotes).strip()
        if len(original_text) > 5 and len(translated_text) > 0:
            length_ratio = len(translated_text) / len(original_text)
            if target_lang in ['am', 'ar', 'hi', 'zh', 'ja', 'ko']:
                if length_ratio < 0.1 or length_ratio > 5.0:
                    return False, f"suspicious_length_ratio_{length_ratio:.2f}"
            elif target_lang:
                if length_ratio < 0.3 or length_ratio > 4.0:
                    return False, f"suspicious_length_ratio_{length_ratio:.2f}"
            else:
                if length_ratio < 0.2 or length_ratio > 4.5:
                    return False, f"suspicious_length_ratio_{length_ratio:.2f}"

        # Check for critical translation artifacts
        bad_patterns = [
            r'^\[.*\]$',  # [UNTRANSLATED]
            r'^TODO:',
            r'^FIXME:',
            r'google translate',
            r'machine translated',
            r'translation error',
            r'^\s*$',  # Only whitespace
            r'[!?.]{3,}',  # Excessive punctuation
        ]
        for pattern in bad_patterns:
            if re.search(pattern, translated_text, re.IGNORECASE):
                return False, "translation_artifact"

        # Allow English proper nouns and technical terms
        allowed_english = ['TensorFlow', 'PyTorch', 'omnipkg', 'Python', 'NumPy', 'SciPy', 'stress-test']
        translated_no_nouns = translated_text
        for noun in allowed_english:
            translated_no_nouns = re.sub(rf'\b{re.escape(noun)}\b', '', translated_no_nouns, flags=re.IGNORECASE)
        # Only flag long, non-technical English words outside quotes
        if re.search(r'\b[A-Za-z]{7,}\b', translated_no_nouns, re.IGNORECASE):
            return False, "translation_artifact_english"

        # Check for emoji preservation
        original_emojis = re.findall(r'[\U0001F300-\U0001F6FF]', original_normalized)
        translated_emojis = re.findall(r'[\U0001F300-\U0001F6FF]', translated_normalized)
        if original_emojis != translated_emojis:
            return False, "emoji_mismatch"

        # Language detection (skipped for short strings or if target_lang is None)
        if target_lang and len(translated_text) > 20:
            try:
                detected_lang = detect(translated_text)
                if detected_lang != target_lang and translated_text:
                    return False, f"language_mismatch_detected_{detected_lang}"
            except LangDetectException:
                if len(translated_text) > 30:
                    return False, "language_detection_failed"

        # Check for proper noun preservation (allow exact matches or transliterations for omnipkg)
        proper_nouns = ['TensorFlow', 'PyTorch', 'omnipkg', 'Python', 'NumPy', 'SciPy', 'stress-test']
        for noun in proper_nouns:
            if noun in original_text:
                # Allow exact match or transliteration for omnipkg
                pattern = rf'\b({re.escape(noun)}|[ሶጎ][ሚኒአይ][ፒኪ][ኪግ])\b' if noun == 'omnipkg' else rf'\b{re.escape(noun)}\b'
                if not re.search(pattern, translated_text, re.IGNORECASE):
                    return False, f"proper_noun_mismatch_{noun}"

        return True, "valid"

    async def _check_language_support(self, endpoint: str, lang_code: str) -> bool:
        """Check if the endpoint supports the target language."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{endpoint.replace('/translate', '/languages')}")
                if response.status_code == 200:
                    languages = response.json()
                    supported_codes = [lang['code'] for lang in languages] if isinstance(languages, list) else []
                    return lang_code in supported_codes
        except:
            pass
        return True  # Assume supported if we can't check

    async def _translate_text_with_retry(self, text: str, dest_lang: str, src_lang: str = 'en') -> Optional[str]:
        """Skip translation for non-linguistic strings."""
        
        # ✅ NEW: Skip translation for formatting/placeholder-only strings
        if self._should_skip_translation(text):
            print(f"    - Skipping: No meaningful content to translate")
            return text  # Return original
            """Smart translation with proper fallback order."""
            # Skip very short strings
            text_content = re.sub(r'\{[^}]*\}', '', text).strip()
            if len(text_content) <= 2:
                return text
            
            # 1. Try DeepL first (most reliable for supported languages)
            deepl_translation = await self._translate_deepl(text, dest_lang, src_lang)
            if deepl_translation:
                is_valid, reason = self._validate_translation(text, deepl_translation)
                if is_valid:
                    return deepl_translation
            
            # 2. Try Google Translate with PROPER placeholder protection
            google_translation = await self._translate_google_fallback(text, dest_lang, src_lang)
            if google_translation:
                return google_translation
            
            # 3. Try LibreTranslate as last resort
            for endpoint in self.translation_endpoints:
                for attempt in range(2):
                    try:
                        async with httpx.AsyncClient(timeout=15) as client:
                            payload = {
                                "q": text,
                                "source": src_lang,
                                "target": dest_lang,
                                "format": "text"
                            }
                            
                            response = await client.post(endpoint, json=payload)
                            
                            if response.status_code == 200:
                                result = response.json()
                                translation = result.get("translatedText", "")
                                
                                if translation:
                                    is_valid, reason = self._validate_translation(text, translation)
                                    if is_valid:
                                        return translation
                                    else:
                                        print(f"    - Invalid translation from {endpoint}: {reason}")
                        
                    except Exception as e:
                        print(f"    - Error with {endpoint}: {e}")
                    
                    await asyncio.sleep(1)
            
            return None

    def _is_translation_suspicious(self, entry, lang_code: str) -> Tuple[bool, str]:
        """Enhanced detection of problematic translations."""
        if not entry.msgstr:
            return True, "empty"
        
        # Check if fuzzy (marked for review)
        if 'fuzzy' in entry.flags:
            return True, "fuzzy"
        
        # Use the improved validation logic
        is_valid, reason = self._validate_translation(entry.msgid, entry.msgstr)
        if not is_valid:
            return True, reason
        
        # Check for suspiciously short translations (language-aware)
        short_threshold = 0.15 if lang_code.startswith(('zh', 'ja', 'ko')) else 0.3
        clean_original = re.sub(r'\{[^}]*\}', '', entry.msgid).strip()
        clean_translation = re.sub(r'\{[^}]*\}', '', entry.msgstr).strip()
        
        if len(clean_translation) < len(clean_original) * short_threshold and len(clean_original) > 10:
            return True, "too_short"
        
        return False, ""

    def _should_skip_translation(self, text: str) -> bool:
        """
        Skip translation for strings with no meaningful linguistic content.
        These are usually formatting, placeholders, or code patterns.
        """
        # Remove placeholders and whitespace first
        clean_text = re.sub(r'\{[^}]*\}', '', text)  # Remove {} placeholders
        clean_text = re.sub(r'[_\-\=\.\*\#\@\!\?]', '', clean_text)  # Remove common punctuation
        clean_text = clean_text.strip()
        
        # If nothing remains after removing placeholders and punctuation, skip
        if not clean_text:
            return True
        
        # Check if it contains any letters or numbers (meaningful content)
        if not re.search(r'[a-zA-Z0-9]', clean_text):
            return True
        
        # Check for very short strings that are just symbols
        if len(clean_text) <= 2 and not any(c.isalnum() for c in clean_text):
            return True
        
        # Check for common formatting patterns
        formatting_patterns = [
            r'^[\=\-\*\_\.\/\\\|~]+$',  # Lines like "====", "----", "****"
            r'^[\+\-\*\/\=\<\>]+$',     # Math/operator patterns
            r'^[\(\)\[\]\{\}]+$',       # Bracket patterns
            r'^[\#\@\!\%\&\^\~]+$',     # Symbol patterns
        ]
        
        if any(re.match(pattern, clean_text) for pattern in formatting_patterns):
            return True
        
        return False
    
    async def _translate_wing_babel_async(self, text: str, dest_lang: str) -> Optional[str]:
        """Async wrapper for the WiNGPT-Babel-2 translator."""
        if not self.wing_babel_translator:
            return None
        try:
            return await asyncio.to_thread(
                self.wing_babel_translator.translate,
                text,
                dest_lang
            )
        except Exception as e:
            logger.error(f"Error during WiNGPT-Babel async execution: {e}")
            return None

    def batch_auto_translate(self, lang_code: str, use_ai: bool = False, non_interactive: bool = False):
        """
        Initiates the batch translation process, now with a non-interactive mode.
        """
        po_file = self.get_po_file(lang_code)
        if not po_file: return
        
        lang_name = self.languages.get(lang_code, lang_code)
        api_dest_lang = self.api_lang_map.get(lang_code, lang_code.split('_')[0])
        
        problematic_entries = [e for e in po_file if e.msgid and self._is_translation_suspicious(e, lang_code)[0]]
        
        if not problematic_entries:
            print(f"🎉 No problematic strings found for {lang_name}!")
            return
        
        if use_ai:
            print(f"🤖 Starting AI-GUIDED batch translation for {lang_name}")
            print(f"🧠 Found {len(problematic_entries)} strings for AI review.")
        else:
            print(f"🌍 Starting GUIDED batch translation for {lang_name}")
            print(f"📝 Found {len(problematic_entries)} strings needing review/translation.")

        if not non_interactive:
            # ... (Your confirmation logic here is fine) ...
            proceed = input(f"\nProceed with this batch? (y/n): ").strip().lower()
            if proceed != 'y':
                print("🚫 Operation cancelled.")
                return

        # --- REFINED LOGIC ---
        # The async function now manages the AI engine's lifecycle entirely.
        # We no longer instantiate TranslationReviewerEngine here.
        if use_ai:
            try:
                # We call the async function without the ai_reviewer argument.
                asyncio.run(self._batch_translate_async_ai(problematic_entries, api_dest_lang, po_file))
            except Exception as e:
                print(f"❌ An unexpected error occurred during the AI batch process: {e}")
                print("⚠️ Falling back to manual interactive mode.")
                asyncio.run(self._batch_translate_async_manual(problematic_entries, api_dest_lang, po_file, lang_code))
        else:
            asyncio.run(self._batch_translate_async_manual(problematic_entries, api_dest_lang, po_file, lang_code))

    async def translate_segment(self, segment: str, dest_lang: str, src_lang: str = 'en') -> str:
            """Translate a single segment with robust retry logic for 429 errors."""
            backoff_time = self.base_backoff
            for attempt in range(self.max_retries):
                try:
                    result = await asyncio.to_thread(
                        self.google_translator.translate,
                        segment, target_language=dest_lang, source_language=src_lang
                    )
                    return result['translatedText'] if result and 'translatedText' in result else ""
                except Exception as e:
                    if '429' in str(e):
                        if attempt < self.max_retries - 1:
                            logger.info(f"Received 429 Too Many Requests for '{segment[:50]}...'. Waiting {backoff_time}s before retry {attempt + 1}/{self.max_retries}")
                            await asyncio.sleep(backoff_time)
                            backoff_time = min(backoff_time * 2, 60)  # Exponential backoff, cap at 60s
                        else:
                            logger.error(f"Google API error after {self.max_retries} retries: {e}")
                            return ""  # Avoid identical_to_original
                    else:
                        logger.error(f"Google API error for '{segment[:50]}...': {str(e)}")
                        return ""
            return ""

    async def _translate_madlad_async(self, text: str, dest_lang: str) -> Optional[str]:
        """Async wrapper to run the synchronous MADLAD translation in a thread."""
        if not self.madlad_translator:
            return None
        try:
            return await asyncio.to_thread(
                self.madlad_translator.translate,
                text,
                dest_lang
            )
        except Exception as e:
            logger.error(f"Error during MADLAD async execution: {e}")
            return None

    async def _translate_mbart_async(self, text: str, dest_lang: str) -> Optional[str]:
        """Async wrapper to run the synchronous mBART translation in a thread."""
        if not self.mbart_translator:
            return None
        
        try:
            # asyncio.to_thread runs the blocking function without freezing the event loop
            return await asyncio.to_thread(
                self.mbart_translator.translate,
                text,
                dest_lang
            )
        except Exception as e:
            logger.error(f"Error during mBART async execution: {e}")
            return None

    async def _run_translation_tournament(self, text: str, dest_lang: str) -> List[TranslationCandidate]:
        """
        Smarter tournament that now includes powerful local GGUF models.
        """
        if self._should_skip_translation(text):
            return [TranslationCandidate(id=1, strategy="skipped", text=text, is_valid=True, validation_reason="no_translatable_content")]

        # Lazy load all heavy models
        if self.madlad_translator is None:
            try:
                self.madlad_translator = MADLADTranslator()
            except Exception as e:
                logger.error(f"Failed to load MADLAD-400 model: {e}")

        if self.wing_babel_translator is None: # <-- LAZY LOAD WINGPT
            try:
                self.wing_babel_translator = WingBabelTranslator()
            except Exception as e:
                logger.error(f"Failed to load WiNGPT-Babel model: {e}")

        # --- THIS IS THE INTELLIGENT PART ---
        has_placeholders = re.search(r'\{[^}]*\}', text)
        strategies = []
        if self.wing_babel_translator:
            strategies.append(("WiNGPT-Babel-2 (Local)", self._translate_wing_babel_async))
        if self.madlad_translator:
            strategies.append(("MADLAD-400", self._translate_madlad_async)) # We will create this next
        if self.mbart_translator:
            strategies.append(("mBART-50", self._translate_mbart_async))
        
        strategies.append(("DeepL", self._translate_deepl))

        # Add the most reliable Google method as a baseline
        strategies.append(("Google (Split v5)", self._translate_google_fallback_v5))

        # ONLY add the extra protection methods if there are placeholders to protect
        if has_placeholders:
            print("\n    - 🛡️ Placeholders detected! Running full protection tournament...")
            strategies.extend([
                ("Google (Protect v1)", self._translate_google_fallback),
                ("Google (Base64 v6)", self._translate_google_fallback_v6),
                ("Google (Numeric v4)", self._translate_google_fallback_v4),
            ])
        # --- END OF INTELLIGENT PART ---
        all_candidates = []
        logger.info(f"Running tournament for: '{text[:70]}...'")

        for name, method in strategies:
            try:
                translation = await method(text, dest_lang)
                if translation:
                    is_valid, reason = self._validate_translation(text, translation, dest_lang)

                    if is_valid:
                        print(f"\n    - ✅ PASSED ({name}): '{translation[:70]}...'")
                    else:
                        print(f"\n    - 👎 REJECTED (Reason: {reason} from {name})")
                        print(f"    - Bad Translation: '{translation}'")

                    candidate = TranslationCandidate(
                        id=len(all_candidates) + 1,
                        strategy=name,
                        text=translation,
                        is_valid=is_valid,
                        validation_reason=reason
                    )
                    all_candidates.append(candidate)
                else:
                    print(f"\n    - ❔ FAILED (No response from {name})")
                
                # No need for extra sleep, the API calls have their own latency
            except Exception as e:
                # Log the error but CONTINUE the loop
                logger.error(f"Critical error in strategy '{name}': {str(e)}")

        return all_candidates

    async def _batch_translate_async_manual(self, problematic_entries, api_dest_lang, po_file, lang_code):
        # This is your existing `_batch_translate_async` which calls `_get_best_translation_interactively`
        # We rename it to make its purpose clear.
        successful_translations = 0
        for i, entry in enumerate(problematic_entries):
            print("\n" + "="*60)
            print(f"Processing string {i+1}/{len(problematic_entries)}")
            chosen_translation = await self._get_best_translation_interactively(entry.msgid, api_dest_lang)
            if chosen_translation:
                entry.msgstr = chosen_translation
                if 'fuzzy' in entry.flags: entry.flags.remove('fuzzy')
                successful_translations += 1
                print(f"  - 💾 Saved: '{chosen_translation[:70]}...'")
            else:
                print(f"  - ⏭️ Skipped.")
        po_file.save()
        print(f"\n💾 Manual translation session complete! Updated {successful_translations} strings.")

    async def _batch_translate_async_ai(self, entries_to_process, api_dest_lang, po_file):
        """
        The async engine that uses an AI to review and select the best translation.
        This version loads the AI reviewer just-in-time for each decision.
        """
        json_preparer = TranslationAISelector()
        successful_translations = 0
        
        for i, entry in enumerate(entries_to_process):
            print(f"\n[{i+1}/{len(entries_to_process)}] Translating '{entry.msgid[:60]}...'")

            # --- EMOJI PRESERVATION LOGIC ---
            emoji_pattern = r'[\U0001F300-\U0001F6FF\u2600-\u26FF\u2700-\u27BF]+'
            leading_emojis = re.match(f'^(\s*{emoji_pattern}+\s*)', entry.msgid)
            trailing_emojis = re.search(f'(\s*{emoji_pattern}+\s*)$', entry.msgid)
            
            text_to_translate = entry.msgid
            prefix = ""
            suffix = ""

            if leading_emojis:
                prefix = leading_emojis.group(1)
                text_to_translate = text_to_translate[len(prefix):]
            if trailing_emojis:
                if len(text_to_translate) > len(trailing_emojis.group(1)):
                    suffix = trailing_emojis.group(1)
                    text_to_translate = text_to_translate[:-len(suffix)]
            # --- END OF EMOJI LOGIC ---

            # This runs the tournament. At this point, no reviewer model is loaded.
            candidates = await self._run_translation_tournament(text_to_translate.strip(), api_dest_lang)
            
            valid_candidates = [c for c in candidates if c.is_valid]
            chosen_translation = None

            if not valid_candidates:
                print("    - ❌ No valid translations found after tournament. Skipping.")
                continue

            if len(valid_candidates) == 1:
                chosen_translation = valid_candidates[0].text
                print(f"    - ✅ Auto-selected the single valid option from [{valid_candidates[0].strategy}]")
            else:
                # Only if we have multiple options, do we load the heavyweight reviewer.
                print(f"    - 🧠 Found {len(valid_candidates)} valid candidates. Sending to AI for final review:")
                for c in valid_candidates:
                    print(f"        - ✅ [{c.strategy}] {c.text[:70]}...")
                
                selection_data = json_preparer.prepare_selection_json(
                    original_text=entry.msgid,
                    target_language=api_dest_lang,
                    candidates=valid_candidates
                )
                
                try:
                    print("    - ⚡ LAZY LOADING AI REVIEWER ENGINE (CodeLlama 13B)...")
                    # Instantiate, use, and implicitly discard the reviewer in a tight scope.
                    ai_reviewer_instance = TranslationReviewerEngine()
                    ai_decision = ai_reviewer_instance.select_best(selection_data)
                    
                    if ai_decision and 'selected_id' in ai_decision:
                        match = next((c for c in valid_candidates if c.id == ai_decision['selected_id']), None)
                        if match:
                            chosen_translation = match.text
                            print(f"    - 🤖 AI Chose: [{match.strategy}] (Reason: {ai_decision.get('reasoning', 'N/A')})")
                        else:
                            print(f"    - ⚠️ AI returned invalid choice ID: {ai_decision['selected_id']}. Skipping.")
                    else:
                        print("    - ⚠️ AI reviewer failed to produce a valid decision. Skipping.")
                except Exception as e:
                    print(f"    - ❌ AI Reviewer Engine failed during execution: {e}")

            if chosen_translation:
                final_text = f"{prefix}{chosen_translation}{suffix}"
                entry.msgstr = final_text
                if 'fuzzy' in entry.flags:
                    entry.flags.remove('fuzzy')
                successful_translations += 1
                print(f"    - 💾 Saved: '{final_text[:70]}...'")

        # Save the file once at the end of the batch.
        po_file.save()
        print("\n" + "="*70)
        print(f"💾 AI-guided translation session complete! Updated {successful_translations} strings.")

    async def _get_best_translation_interactively(self, text: str, dest_lang: str) -> Optional[str]:
        """
        Runs a "translation tournament" and shows ALL valid results,
        now with an option to use the original text.
        """
        print(f"  - Original: '{text[:70]}...'")

        strategies = [
            ("DeepL", self._translate_deepl),
            ("Google (Protect v1)", self._translate_google_fallback),
            ("google (Protect v2)", self._translate_google_fallback_v2),  # Added the split version as well
            ("Google (HTML v3)", self._translate_google_fallback_v3),
            ("Google (Numeric v4)", self._translate_google_fallback_v4),  # Fixed typo
            ("Google (Split v5)", self._translate_google_fallback_v5),
            ("Google (Base64 v6)", self._translate_google_fallback_v6),
            ('Google (Unicode Stealth v7)', self._translate_google_fallback_v7),  # NEW
            ('Google (Emoji Protect v8)', self._translate_google_fallback_v8),    # NEW
        ]

        valid_translations = []

        for name, method in strategies:
            print(f"    - Trying strategy: {name}...", end='', flush=True)
            await asyncio.sleep(1.5)
            
            translation = await method(text, dest_lang)

            if translation:
                is_valid, reason = self._validate_translation(text, translation)
                if is_valid:
                    print(" ✅ Valid")
                    valid_translations.append({"name": name, "text": translation})
                else:
                    print(f" ❌ Invalid ({reason})")
            else:
                print(" ❌ Failed")

        # --- Interactive Choice Logic ---
        if not valid_translations:
            print("    - ❌ All translation strategies failed.")
            # Offer manual or original even on full failure
            choice = input("  - Enter manual translation (m), use original (o), or skip (s): ").strip().lower()
            if choice == 'm':
                manual_text = input("  - Enter your corrected translation: ").strip()
                return manual_text if manual_text else None
            elif choice == 'o':
                return text
            else:
                return None

        print("\n  - Reviewing all valid translations. Please choose the best one:")
        for i, trans in enumerate(valid_translations):
            print(f"    {i+1}) [{trans['name']}] {trans['text']}")
        
        print("    -----------------------------------------")
        print("    m) Enter a Manual correction")
        # --- THIS IS THE NEW OPTION ---
        print("    o) Use Original Text (don't translate)")
        print("    s) Skip (keep current or leave empty)")

        while True:
            # --- PROMPT UPDATED ---
            choice = input("  - Your choice (1, 2, ..., m, o, s): ").strip().lower()
            
            if choice == 's':
                return None # Signals "do not change"
            
            # --- NEW LOGIC HANDLER ---
            if choice == 'o':
                return text # Return the original string to be saved

            if choice == 'm':
                manual_text = input("  - Enter your corrected translation: ").strip()
                return manual_text if manual_text else None
            try:
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(valid_translations):
                    return valid_translations[choice_idx]["text"]
                else:
                    print("    - Invalid number. Please try again.")
            except ValueError:
                print("    - Invalid input. Please enter a number, 'm', 'o', or 's'.")

    async def _batch_translate_async(self, problematic_entries, api_dest_lang, po_file, lang_code):
        """
        The async engine that uses an AI to review and select the best translation.
        This version correctly calls the translation tournament and handles the AI interaction.
        """
        # Initialize the AI and the JSON preparer
        ai_reviewer = self.ai_reviewer
        json_preparer = TranslationAISelector(None) # We only need its prepare_selection_json method
        
        successful_translations = 0
        
        try:
            prepare_for_ai_task()
            
            for i, entry in enumerate(problematic_entries):
                print("\n" + "="*70)
                print(f"Processing string {i+1}/{len(problematic_entries)}: '{entry.msgid[:60]}...'")
                
                # --- THIS IS THE FIX ---
                # Call the tournament function to get the list of valid candidates.
                # This replaces the broken placeholder line.
                valid_translations = await self.__get_best_translation_interactively(entry.msgid, api_dest_lang)
                # --- END OF FIX ---

                chosen_translation = None
                if not valid_translations:
                    print("    - ❌ All automated strategies failed. Skipping.")
                elif len(valid_translations) == 1:
                    chosen_translation = valid_translations[0].text
                    print(f"  - ✅ Automatically selected the single valid translation: '{chosen_translation[:70]}...'")
                else:
                    print(f"  - 🧠 Preparing data for AI reviewer with {len(valid_translations)} options...")
                    print(f"    - 🧠 Found {len(candidates)} valid candidates. Sending to AI for review:")
                    for c in candidates:
                        print(f"        - [{c.strategy}] {c.text[:70]}...")
                    # The TranslationCandidate objects are already created by the tournament function
                    
                    # Prepare the JSON prompt using your class
                    selection_data = json_preparer.prepare_selection_json(
                        original_text=entry.msgid,
                        target_language=api_dest_lang,
                        candidates=valid_translations # Pass the list of candidates directly
                    )
                    
                    # Get the AI's decision
                    ai_decision = ai_reviewer.select_best(selection_data)
                    
                    if ai_decision and 'selected_id' in ai_decision:
                        selected_id = ai_decision['selected_id']
                        if 1 <= selected_id <= len(valid_translations):
                            chosen_translation = valid_translations[selected_id - 1].text
                            print(f"  - 🤖 AI Chose Option #{selected_id}: [{valid_translations[selected_id - 1].strategy}]")
                            print(f"  - Reasoning: {ai_decision.get('reasoning', 'N/A')}")
                        else:
                            print(f"  - ⚠️ AI reviewer returned an invalid choice ID: {selected_id}. Skipping.")
                    else:
                        print("  - ⚠️ AI reviewer failed to provide a valid decision. Skipping.")
                
                if chosen_translation:
                    entry.msgstr = chosen_translation
                    if 'fuzzy' in entry.flags: entry.flags.remove('fuzzy')
                    successful_translations += 1
                    print(f"  - 💾 Saved.")
                else:
                    print(f"  - ⏭️ Skipped.")

        finally:
            resume_mining_operations()
            po_file.save()
            print("\n" + "="*70)
            print(f"\n💾 AI-guided translation session complete! Updated {successful_translations} strings.")


    # Keep all your other existing methods unchanged
    def show_status(self):
        print("📊 Translation Status (Enhanced Analysis):")
        print("-" * 80)
        pot_path = self.locale_dir / 'omnipkg.pot'
        if not pot_path.exists():
            print("Master .pot file not found. Run extraction (Option 5) first.")
            return
            
        pot_file = polib.pofile(str(pot_path))
        total_strings = len(pot_file)
        
        if not total_strings:
            print("No translatable strings found in the master template.")
            return
        
        print(f"{'Language':<20} {'Code':<6} {'Good':<4} {'Issues':<6} {'Complete %':<10} {'Quality Issues'}")
        print("-" * 80)
        
        for lang_code, lang_name in self.languages.items():
            po_file = self.get_po_file(lang_code)
            if not po_file:
                continue
                
            good_translations = 0
            issue_count = 0
            issues_breakdown = {
                'empty': 0, 'fuzzy': 0, 'identical': 0, 
                'placeholder_mismatch': 0, 'too_short': 0, 'translation_artifact': 0
            }
            
            for entry in po_file:
                if entry.msgid:  # Skip header entries
                    is_suspicious, reason = self._is_translation_suspicious(entry, lang_code)
                    if is_suspicious:
                        issue_count += 1
                        if reason in issues_breakdown:
                            issues_breakdown[reason] += 1
                        else:
                            issues_breakdown['translation_artifact'] += 1
                    else:
                        good_translations += 1
            
            percent = (good_translations / total_strings) * 100
            
            # Create issues summary
            issues_list = []
            for issue_type, count in issues_breakdown.items():
                if count > 0:
                    if issue_type == 'empty':
                        issues_list.append(f"{count} empty")
                    elif issue_type == 'fuzzy':
                        issues_list.append(f"{count} fuzzy")
                    elif issue_type == 'identical':
                        issues_list.append(f"{count} identical")
                    elif issue_type == 'placeholder_mismatch':
                        issues_list.append(f"{count} placeholder")
                    elif issue_type == 'too_short':
                        issues_list.append(f"{count} short")
                    elif issue_type == 'translation_artifact':
                        issues_list.append(f"{count} artifact")
            
            issues_summary = ', '.join(issues_list) if issues_list else 'None'
            
            # Color coding based on quality
            if percent >= 95:
                status_icon = "🟢"
            elif percent >= 80:
                status_icon = "🟡" 
            else:
                status_icon = "🔴"
                
            print(f"{status_icon} {lang_name:<18} {lang_code:<6} {good_translations:<4} {issue_count:<6} {percent:>7.1f}%   {issues_summary}")

    def show_detailed_issues(self, lang_code: str):
        """Show detailed list of problematic translations for a specific language."""
        po_file = self.get_po_file(lang_code)
        if not po_file:
            return
        
        lang_name = self.languages.get(lang_code, lang_code)
        print(f"\n🔍 Detailed Issues Analysis for {lang_name} ({lang_code})")
        print("=" * 60)
        
        issues_found = False
        issue_categories = {
            'empty': [], 'fuzzy': [], 'identical': [], 
            'placeholder_mismatch': [], 'too_short': [], 'translation_artifact': []
        }
        
        for entry in po_file:
            if entry.msgid:
                is_suspicious, reason = self._is_translation_suspicious(entry, lang_code)
                if is_suspicious:
                    if reason in issue_categories:
                        issue_categories[reason].append(entry)
                    else:
                        issue_categories['translation_artifact'].append(entry)
                    issues_found = True
        
        if not issues_found:
            print(f"🎉 No translation issues found for {lang_name}!")
            return
        
        for category, entries in issue_categories.items():
            if entries:
                category_names = {
                    'empty': '❌ Empty Translations',
                    'fuzzy': '🔄 Fuzzy (Needs Review)',
                    'identical': '🔄 Identical to Source',
                    'placeholder_mismatch': '📝 Placeholder Mismatch',
                    'too_short': '📏 Suspiciously Short',
                    'translation_artifact': '🤖 Translation Artifacts'
                }
                
                print(f"\n{category_names[category]} ({len(entries)} issues):")
                print("-" * 40)
                
                for i, entry in enumerate(entries[:10]):  # Show first 10
                    print(f"  {i+1}. Original: {entry.msgid}")
                    print(f"     Current:  {entry.msgstr if entry.msgstr else '(empty)'}")
                    if 'fuzzy' in entry.flags:
                        print(f"     Status:   FUZZY - needs review")
                    print()
                
                if len(entries) > 10:
                    print(f"     ... and {len(entries) - 10} more issues")
                print()

    def add_new_language(self, lang_code: str):
        """Creates the directory structure and .po file for a new language."""
        print(f"Adding new language: {lang_code}...")
        lang_dir = self.locale_dir / lang_code / "LC_MESSAGES"
        if lang_dir.exists():
            print(f"❌ Error: Language '{lang_code}' already exists.")
            return

        pot_file = self.locale_dir / 'omnipkg.pot'
        if not pot_file.exists():
            print("❌ Error: Master template 'omnipkg.pot' not found. Run extraction (Option 5) first.")
            return

        lang_dir.mkdir(parents=True, exist_ok=True)
        po_path = lang_dir / 'omnipkg.po'
        shutil.copy(pot_file, po_path)
        print(f"✓ Successfully created '{po_path}'.")
        
        # Refresh the internal list of languages so the next command works
        self.languages = self._discover_languages()
        print(f"✓ Language list refreshed. '{lang_code}' is now ready for translation.")

    def find_and_edit_string(self, lang_code: str):
        """Finds a specific string by its original English text and allows editing."""
        po_file = self.get_po_file(lang_code)
        if not po_file: 
            return

        search_term = input("\nEnter a part of the original English text to find: ").strip()
        if not search_term: 
            return

        matches = [entry for entry in po_file if search_term.lower() in entry.msgid.lower()]

        if not matches:
            print(f"❌ No string containing '{search_term}' found.")
            return

        if len(matches) == 1:
            entry_to_edit = matches[0]
        else:
            print("\nMultiple matches found. Please choose which one to edit:")
            for i, entry in enumerate(matches, 1):
                is_suspicious, reason = self._is_translation_suspicious(entry, lang_code)
                status = f" [{reason}]" if is_suspicious else " [OK]"
                print(f"  {i}) {entry.msgid}{status}")
            try:
                choice = int(input("Enter number: ").strip())
                entry_to_edit = matches[choice - 1]
            except (ValueError, IndexError):
                print("❌ Invalid selection.")
                return

        print("\n--- Editing String ---")
        print(f"📄 Original: {entry_to_edit.msgid}")
        print(f"💬 Current:  {entry_to_edit.msgstr}")
        
        is_suspicious, reason = self._is_translation_suspicious(entry_to_edit, lang_code)
        if is_suspicious:
            print(f"⚠️  Issue:    {reason}")
        
        new_translation = input("🌍 Enter new translation: ").strip()

        if new_translation:
            is_valid, validation_reason = self._validate_translation(entry_to_edit.msgid, new_translation)
            if is_valid:
                entry_to_edit.msgstr = new_translation
                # Remove fuzzy flag if present
                if 'fuzzy' in entry_to_edit.flags:
                    entry_to_edit.flags.remove('fuzzy')
                po_file.save()
                print("✓ Translation updated successfully!")
            else:
                print(f"⚠️ Warning: Translation validation failed ({validation_reason}). Save anyway? (y/n)")
                if input().strip().lower() == 'y':
                    entry_to_edit.msgstr = new_translation
                    if 'fuzzy' in entry_to_edit.flags:
                        entry.flags.remove('fuzzy')
                    po_file.save()
                    print("✓ Translation saved (with warnings).")
                else:
                    print("🚫 Translation not saved.")
        else:
            print("🚫 No change made.")

    def fix_technical_errors(self, target_lang: Optional[str] = None):
        """
        Orchestrates the process of finding and fixing technically broken translations.
        This version trusts the FixerEngine's validation and removes the redundant check.
        """
        languages_to_process = [target_lang] if target_lang else [lang for lang in self.languages.keys() if lang != 'en']
        
        ai_fixer = None 

        for lang_code in languages_to_process:
            print(f"\n--- Fixing technically broken translations for {lang_code} ---")
            po_file = self.get_po_file(lang_code)
            if not po_file: continue

            fuzzy_entries = [entry for entry in po_file if 'fuzzy' in entry.flags]

            if not fuzzy_entries:
                print("    ✅ No translations flagged for fixing.")
                continue
            
            print(f"    - Found {len(fuzzy_entries)} translations to fix.")
            
            if ai_fixer is None:
                # Lazily initialize the engine only if there's work to do
                try:
                    ai_fixer = TranslationFixerEngine()
                except (ConnectionError, FileNotFoundError) as e:
                    print(f"    - ❌ Could not initialize AI Fixer Engine: {e}")
                    break # Stop processing if the engine can't start

            fixed_count = 0
            for i, entry in enumerate(fuzzy_entries):
                # Get the initial reason for being fuzzy to pass to the AI for context
                _, reason = self._validate_translation(entry.msgid, entry.msgstr)

                print(f"    - Fixing string {i+1}/{len(fuzzy_entries)} (Reason: {reason}): '{entry.msgstr[:50]}...'")
                
                success, fix_reason, fix_result = ai_fixer.fix_translation(
                    original=entry.msgid, 
                    broken=entry.msgstr, 
                    language=lang_code, 
                    reason=reason
                )
                
                # --- THIS IS THE CORRECTED LOGIC ---
                # If the specialized FixerEngine reports success, we trust its judgment.
                # No more redundant validation is needed.
                if success and 'fixed_translation' in fix_result:
                    fixed_text = fix_result['fixed_translation']
                    print(f"      - ✨ AI Fix successful ({fix_reason}): '{fixed_text[:50]}...'")
                    entry.msgstr = fixed_text
                    entry.flags.remove('fuzzy') # The fix is applied, so it's no longer fuzzy
                    fixed_count += 1
                else:
                    # This branch is hit if the AI fails, times out, or returns invalid data.
                    print(f"      - ⚠️ AI failed to provide a fix ({fix_reason}). Leaving as fuzzy.")
            
            if fixed_count > 0:
                print(f"    💾 Repaired and saved {fixed_count} translations for {lang_code}.")
                po_file.save()

    def run_interactive_session(self, lang_code: str, review_mode: bool = False):
        """Enhanced interactive session with validation."""
        po_file = self.get_po_file(lang_code)
        if not po_file: 
            return
        
        lang_name = self.languages.get(lang_code, lang_code)
        
        if review_mode:
            entries = po_file
            mode_text = "Reviewing ALL"
        else:
            # Get problematic entries instead of just untranslated ones
            entries = []
            for entry in po_file:
                if entry.msgid:  # Skip header entries
                    is_suspicious, _ = self._is_translation_suspicious(entry, lang_code)
                    if is_suspicious:
                        entries.append(entry)
            mode_text = "Fixing PROBLEMATIC"
        
        if not entries:
            print(f"🎉 No strings to process in this mode for {lang_name}!")
            return
            
        print(f"🌍 {mode_text} strings for {lang_name} ({lang_code})")
        print("   (Enter 'q' to quit, 's' to skip)")
        print(f"📝 Found {len(entries)} strings to process")
        
        for i, entry in enumerate(entries, 1):
            print(f"[{i}/{len(entries)}] Original:")
            print(f"📄 {entry.msgid}")
            
            if entry.msgstr: 
                print(f"💬 Current: {entry.msgstr}")
            
            is_suspicious, reason = self._is_translation_suspicious(entry, lang_code)
            if is_suspicious:
                print(f"⚠️  Issue: {reason}")
            
            user_input = input("🌍 Your translation: ").strip()
            
            if user_input.lower() == 'q': 
                break
            elif user_input.lower() == 's': 
                print("⏭ Skipped"); continue
            elif user_input:
                is_valid, validation_reason = self._validate_translation(entry.msgid, user_input)
                if is_valid:
                    entry.msgstr = user_input
                    # Remove fuzzy flag if present
                    if 'fuzzy' in entry.flags:
                        entry.flags.remove('fuzzy')
                    print("✓ Saved.")
                else:
                    print(f"⚠️ Warning: {validation_reason}. Save anyway? (y/n)")
                    if input().strip().lower() == 'y':
                        entry.msgstr = user_input
                        if 'fuzzy' in entry.flags:
                            entry.flags.remove('fuzzy')
                        print("✓ Saved (with warnings).")
                    else:
                        print("⏭ Not saved.")
            elif not user_input and entry.msgstr: 
                print("✓ Kept existing.")
            else: 
                print("⏭ Skipped.")
            print("-" * 20)
        
        po_file.save()
        print("\n💾 Session finished. All changes have been saved.")


def run_external_script(args):
    """Helper to run the extract_strings.py script."""
    # Assumes extract_strings.py is in the same directory as this helper
    script_path = Path(__file__).parent / 'extract_strings.py'

    if not script_path.exists():
        print(f"❌ Error: {script_path.name} not found in the same directory as this helper.")
        return
    
    command = ['python', str(script_path)] + args
    print(f"Running command: {' '.join(command)}")
    # Run the command from the current working directory, which should be the project root
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    
    if result.returncode == 0:
        print("✓ Operation successful.")
        print(result.stdout)
    else:
        print(f"❌ Operation failed.")
        print(f"   STDOUT: {result.stdout}")
        print(f"   STDERR: {result.stderr}")

def main():
    """Main interactive menu"""
    helper = TranslationHelper()
    
    while True:
        print("\n🌍 OmniPkg Translation Management Suite (Enhanced)")
        print("==================================================")
        print("1. Show translation status (enhanced)")
        print("2. Interactive Session (Translate or Edit)")
        print("3. Batch auto-translate a language")
        print("4. Add a new language")
        print("5. Extract new strings from code")
        print("6. Compile all translations")
        print("7. Test translation")
        print("8. Show detailed issues for a language")
        print("9. 🔧 Fix Underscore Variables (Lazy _ placeholders)")
        print("0. Exit")
        
        choice = input("\n🔤 Choose option: ").strip()
        
        if choice == '0': 
            break
        elif choice == '1': 
            helper.show_status()
        elif choice == '2':
            lang = input("Enter language code (e.g., es): ").strip()
            if lang in helper.languages:
                print("\nChoose a session type:")
                print("  1. Fix only PROBLEMATIC strings (recommended)")
                print("  2. Review ALL strings from the beginning")
                print("  3. Find & Edit a specific string")
                mode_choice = input("Select mode (1, 2, or 3): ").strip()
                if mode_choice == '1': 
                    helper.run_interactive_session(lang, review_mode=False)
                elif mode_choice == '2': 
                    helper.run_interactive_session(lang, review_mode=True)
                elif mode_choice == '3': 
                    helper.find_and_edit_string(lang)
                else: 
                    print("❌ Invalid mode choice.")
            else: 
                print("❌ Invalid language code")
        elif choice == '3':
            lang = input("Enter language code to batch translate (e.g., fr): ").strip()
            if lang in helper.languages:
                confirm = input(f"⚠️  This will auto-translate ALL problematic strings for {helper.languages[lang]}. Proceed? (y/n): ").strip().lower()
                if confirm == 'y': 
                    helper.batch_auto_translate(lang)
                else: 
                    print("🚫 Operation cancelled.")
            else: 
                print("❌ Invalid language code")
        elif choice == '4':
            new_lang = input("Enter the new language code (e.g., 'da' for Danish): ").strip().lower()
            if new_lang: 
                helper.add_new_language(new_lang)
            else: 
                print("❌ Language code cannot be empty.")
        elif choice == '5': 
            run_external_script([])
        elif choice == '6': 
            run_external_script(['--compile-only'])
        elif choice == '7':
            lang = input("Enter language code to test (e.g., 'es'): ").strip()
            if lang:
                print("-" * 40)
                print("📋 Copy and paste this command into your terminal to test:")
                print(f"LANG={lang} omnipkg --help")
                print("-" * 40)
        elif choice == '8':
            lang = input("Enter language code to analyze (e.g., 'es'): ").strip()
            if lang in helper.languages:
                helper.show_detailed_issues(lang)
            else:
                print("❌ Invalid language code")
        elif choice == '9':
            underscore_fixer.main_menu()
        else: 
            print("❌ Invalid choice")

if __name__ == '__main__':
    main()
