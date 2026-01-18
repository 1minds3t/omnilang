#!/usr/bin/env python3
"""
OmniPkg i18n Management Super-Tool (V3 - Interactive Edition)
A unified CLI to control the entire localization pipeline.
Features an interactive menu for easy use and direct commands for scripting.
"""
import sys
import argparse
from pathlib import Path
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import subprocess
from omnilang.ai.AuditorWrapper import TranslationAuditorEngine
from omnilang.ai.QualityWrapper import TranslationQualityAuditorEngine
from omnilang.ai.FixerEngine import TranslationFixerEngine
from omnilang.ai.RefinerEngine import TranslationRefinerEngine
from omnilang.ai.TranslatorWrapper import (
    TranslationReviewerEngine,
    TranslationAISelector,
    TranslationCandidate
)
from omnilang.core.helper import TranslationHelper, main as translation_helper_main
from omnilang.core import extractor as extract_strings
import os
import signal
import time
from contextlib import contextmanager
import threading
import logging
import shutil
import tempfile
from omnilang.core.converter import ASTFStringConverter
from omnilang.core.helper import TranslationHelper, main as translation_helper_main
from omnilang.core import extractor as extract_strings
from omnilang.core import underscore_fixer

import json

llama_path = "/home/minds3t/stealth/llama.cpp/build_cuda/bin/llama-cli"
model_path = "/ai_data/lollama/.lollama/blobs/WiNGPT-Babel-2-Q4_K_M.gguf"

# --- Add the i18n_tools directory to the path to import our modules ---
# This is the only sys.path modification needed.
TOOLS_DIR = Path(__file__).parent.resolve()
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# --- Import from your local tool files ---
# This will now work because TOOLS_DIR is in the path.
from omnilang.core.converter import ASTFStringConverter
from omnilang.core.helper import TranslationHelper, main as translation_helper_main
from omnilang.core import extractor as extract_strings

import json
from pathlib import Path
import os

from omnilang.common_utils import get_project_root, detect_source_directories

PROJECT_ROOT = get_project_root()

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

# ======================================================================
# Interactive Menu (for convenience)
# ======================================================================
def get_language_input(available_langs, prompt="Enter language code (e.g., 'es' for Spanish): "):
    """Get and validate language code input from user."""
    while True:
        lang_code = input(prompt).strip()
        if not lang_code:
            print("❌ Language code cannot be empty.")
            continue
        if lang_code in available_langs:
            return lang_code
        else:
            print(f"❌ Language '{lang_code}' not found.")
            print(f"Available languages: {', '.join(sorted(available_langs))}")
            continue

def get_dry_run_input():
    """Ask user if they want to do a dry run."""
    while True:
        choice = input("Perform dry run? (y/N): ").strip().lower()
        if choice in ['', 'n', 'no']:
            return False
        elif choice in ['y', 'yes']:
            return True
        else:
            print("❌ Please enter 'y' for yes or 'n' for no.")

def run_interactive_menu():
    """Presents a user-friendly menu when the script is run without arguments."""
    
    # Load and display the actual configured project
    config_home = os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')
    config_path = Path(config_home) / 'omnilang' / 'i18n_config.json'

    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                project_root = Path(config.get('project_root'))
                print(f"📂 Active Project: {project_root}\n")
        except:
            print(f"📂 Active Project: {get_project_root()}\n")
    else:
        print(f"📂 Active Project: {get_project_root()}\n")
    print("🚀 Welcome to the OmniPkg i18n Management Super-Tool!")
    print("======================================================")
    
    available_langs = []
    try:
        # Only try to load helper if we're in a valid project directory
        if (get_project_root() / 'locale').exists() or (get_project_root() / 'src').exists():
            helper = TranslationHelper()
            available_langs = list(helper.languages.keys())
            if available_langs:
                print(f"Found {len(available_langs)} languages: {', '.join(available_langs)}")
            else:
                print("⚠️  No languages found.")
        else:
            print("⚠️  No project configured. Use option 19 to set up your project.")
    except Exception as e:
        print(f"⚠️  Could not scan for languages: {e}")
        
    # Corrected Menu Printout
    print("\nPlease choose an operation by typing its number:")
    print("\n📋 COMMON OPERATIONS:")
    print("  1) Run the full pipeline (Refactor -> Extract -> Translate -> Compile)")
    print("  2) Show translation status for all languages")
    print("  3) Auto-translate all missing strings (all languages)")
    print("  4) Compile all .po files to binary .mo files")
    
    print("\n🔧 DEVELOPMENT OPERATIONS:")
    print("  5) Safely prepare Python source code for translation")
    print("  6) Extract all translatable strings into a .pot template file")
    
    print("\n🌍 LANGUAGE-SPECIFIC OPERATIONS:")
    print("  7) Add a new language")
    print("  8) Auto-translate a specific language") 
    print("  9) Show detailed issues for a language")
    print(" 10) Fix/edit translations for a language (interactive)")
    print(" 11) Test a language translation")

    print("\n🤖 AI-POWERED REFINEMENT:")
    print(" 12) Audit all translations for code-breaking errors with AI")
    print(" 13) Rescan and Fix flagged translations with AI")
    print(" 15) Audit for LINGUISTIC QUALITY with AI (Slower, Comprehensive)")
    print(" 16) REFINE low-quality translations with AI (Audit -> Fix -> Save)")
    print(" 17) FIX technically broken (fuzzy) translations with AI")
    print(" 18) REFINE(v2) low-quality translations with AI (Audit -> Fix -> Save)")
    print("\n⚙️ CONFIGURATION:")
    print(" 19) 🔄 Switch Active Project")
    print("\n🚀 EXPERT MODE:")
    print(" 14) Launch full interactive expert-mode helper")


    print("\n  q) Quit")

    while True:
        try:
            choice = input("\nEnter your choice: ").strip().lower()

            if choice == 'q':
                print("👋 Exiting tool. Goodbye!")
                break
            
            choice_num = int(choice)

            default_args = argparse.Namespace(
                lang=None,
                dry_run=False,
                ai=False,
                skip_refactor=False,
                skip_translate=False,
                lang_code=None,
                review_all=False,
                no_backup=False,
                threshold=3
            )
            
            print()
            
            if choice_num == 1:
                print("--- Running: Complete i18n Pipeline ---\n")
                dry_run = get_dry_run_input()
                ai_choice = input("Use AI Reviewer for the translation step? (y/N): ").strip().lower()
                use_ai = ai_choice in ['y', 'yes']
                default_args.dry_run = dry_run
                default_args.ai = use_ai
                run_pipeline(default_args)
            elif choice_num == 2:
                print("--- Running: Translation Status ---\n")
                run_status(default_args)
            elif choice_num == 3:
                print("--- Running: Auto-translate All Languages ---")
                ai_choice = input("Use AI Reviewer to select the best translation? (y/N): ").strip().lower()
                use_ai = ai_choice in ['y', 'yes']
                default_args.lang = None
                default_args.ai = use_ai
                run_translate(default_args)
            elif choice_num == 4:
                print("--- Running: Compile All Translations ---\n")
                run_compile(default_args)
            elif choice_num == 5:
                print("--- Running: Code Refactor ---\n")
                dry_run = get_dry_run_input()
                default_args.dry_run = dry_run
                if not dry_run:
                    backup_choice = input("Create backups? (Y/n): ").strip().lower()
                    default_args.no_backup = backup_choice in ['n', 'no']
                run_refactor(default_args)
            elif choice_num == 6:
                print("--- Running: String Extraction ---\n")
                run_extract(default_args)
            elif choice_num == 7:
                print("--- Running: Add New Language ---")
                lang_code = input("Enter the new language code (e.g., 'el' for Greek): ").strip()
                if lang_code:
                    default_args.lang_code = lang_code
                    run_add_lang(default_args)
                else:
                    print("❌ Language code cannot be empty.")
                    continue
            elif choice_num == 8:
                print("--- Running: Auto-translate Specific Language ---")
                lang_code = get_language_input(available_langs)
                ai_choice = input("Use AI Reviewer to select the best translation? (y/N): ").strip().lower()
                use_ai = ai_choice in ['y', 'yes']
                default_args.lang = lang_code
                default_args.ai = use_ai
                run_translate(default_args)
            elif choice_num == 9:
                print("--- Running: Show Language Issues ---")
                lang_code = get_language_input(available_langs)
                default_args.lang_code = lang_code
                run_issues(default_args)
            elif choice_num == 10:
                print("--- Running: Fix/Edit Language Translations ---")
                lang_code = get_language_input(available_langs)
                review_choice = input("Review all strings? [y/N]: ").strip().lower()
                review_all = review_choice in ['y', 'yes']
                default_args.lang_code = lang_code
                default_args.review_all = review_all
                run_fix_interactive(default_args)
            elif choice_num == 11:
                print("--- Running: Test Language Translation ---")
                lang_code = get_language_input(available_langs)
                default_args.lang_code = lang_code
                run_test(default_args)
            elif choice_num == 12:
                print("--- Running: AI-Powered Translation Audit ---")
                lang_code = input("Enter language to audit (or leave blank for all): ").strip()
                default_args.lang = lang_code if lang_code else None
                run_audit(default_args)
            elif choice_num == 13:
                print("--- Running: AI-Powered Rescan & Refinement ---")
                lang_code = input("Enter language to rescan (or leave blank for all): ").strip()
                default_args.lang = lang_code if lang_code else None
                run_rescan(default_args)
            elif choice_num == 14:
                print("--- Running: Expert Mode ---\n")
                run_interactive(default_args)
            elif choice_num == 15:
                print("--- Running: AI-Powered Linguistic Quality Audit ---")
                lang_code = input("Enter language for quality audit (or leave blank for all): ").strip()
                default_args.lang = lang_code if lang_code else None
                run_quality_audit(default_args)
            elif choice_num == 16:
                print("--- Running: AI-Powered Translation Refinement ---")
                lang_code = input("Enter language to refine (or leave blank for all): ").strip()
                default_args.lang = lang_code if lang_code else None
                run_refine(default_args)
            elif choice_num == 17:
                print("--- Running: AI-Powered Technical Fixer ---")
                lang_code = input("Enter language to fix (or leave blank for all): ").strip()
                default_args.lang = lang_code if lang_code else None
                run_fix(default_args)
            elif choice_num == 18:
                print("--- Running: AI Refinement v2 ---")
                lang_code = input("Enter language (or blank for all): ").strip()
                default_args.lang = lang_code if lang_code else None
                run_refinev2(default_args)
            elif choice_num == 19:
                run_switch_project(None)
            else:
                print(f"❌ Invalid choice. Please enter a number between 1 and 18.")
                continue
            
            print(f"\n--- ✅ Operation completed! ---\n")
            break
            
        except ValueError:
            print("❌ Invalid input. Please enter a number.")
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Exiting tool. Goodbye!")
            break

# ======================================================================
# Main CLI Logic (for scripting and direct commands)
# ======================================================================
def main():
    """Main function to parse arguments and dispatch commands."""
    if len(sys.argv) == 1:
        run_interactive_menu()
        return

    parser = argparse.ArgumentParser(
        prog='i18n_cli.py',
        description='🚀 The all-in-one i18n management tool for your project.',
        epilog='Run a command with --help for specific options (e.g., i18n_cli.py refactor --help).'
    )
    
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

    # --- ALL SUBPARSER DEFINITIONS ---
    p_pipeline = subparsers.add_parser('pipeline', help='Runs the complete i18n pipeline: refactor -> extract -> translate -> compile.')
    p_pipeline.add_argument('--skip-refactor', action='store_true', help='Skip the initial code refactoring step.')
    p_pipeline.add_argument('--skip-translate', action='store_true', help='Skip the auto-translation step.')
    p_pipeline.add_argument('--lang', help='Translate only a specific language code during the pipeline.')
    p_pipeline.add_argument('--ai', action='store_true', help='Use the AI reviewer to select the best translation.')
    p_pipeline.add_argument('--dry-run', action='store_true', help='Preview changes without modifying files.')
    p_pipeline.set_defaults(func=run_pipeline)

    p_refactor = subparsers.add_parser('refactor', help='Safely prepares all Python source code for translation.')
    p_refactor.add_argument('--dry-run', action='store_true', help='Preview changes without modifying files.')
    p_refactor.add_argument('--no-backup', action='store_true', help='Do not create backups (not recommended).')
    p_refactor.set_defaults(func=run_refactor)

    p_extract = subparsers.add_parser('extract', help='Extracts all translatable strings into a .pot template file.')
    p_extract.set_defaults(func=run_extract)

    p_translate = subparsers.add_parser('translate', help='Auto-translates all missing strings for all languages.')
    p_translate.add_argument('--lang', help='Translate only a specific language code (e.g., "es").')
    p_translate.add_argument('--ai', action='store_true', help='Use the AI reviewer to select the best translation.')
    p_translate.set_defaults(func=run_translate)
    p_switch = subparsers.add_parser('switch-project', help='Switch to a different project directory.')
    p_switch.set_defaults(func=run_switch_project)
    p_compile = subparsers.add_parser('compile', help='Compiles all .po files to binary .mo files.')
    p_compile.set_defaults(func=run_compile)

    p_status = subparsers.add_parser('status', help='Shows the current translation status for all languages.')
    p_status.set_defaults(func=run_status)

    p_audit = subparsers.add_parser('audit', help='Audits all translations for code-breaking errors using an AI.')
    p_audit.add_argument('--lang', help='Audit only a specific language code (e.g., "ja").')
    p_audit.set_defaults(func=run_audit)

    p_rescan = subparsers.add_parser('rescan', help='Rescans all existing translations for quality issues and sends them to the AI for fixing.')
    p_rescan.add_argument('--lang', help='Rescan only a specific language code (e.g., "ja").')
    p_rescan.set_defaults(func=run_rescan)

    p_quality = subparsers.add_parser('quality-audit', help='Audits all translations for linguistic quality and fluency using an AI.')
    p_quality.add_argument('--lang', help='Audit only a specific language code (e.g., "ja").')
    p_quality.set_defaults(func=run_quality_audit)

    p_refine = subparsers.add_parser('refine', help='Finds low-quality translations and uses an AI to fix and replace them.')
    p_refine.add_argument('--lang', help='Refine only a specific language.')
    p_refine.add_argument('--threshold', type=int, default=3, help='Quality score at or below which a translation is refined (default: 3).')
    p_refine.set_defaults(func=run_refine)

    p_refinev2 = subparsers.add_parser('refinev2', help='Ultimate AI refinement with quality audit and multiple AI engines.')
    p_refinev2.add_argument('--lang', help='Refine only a specific language.')
    p_refinev2.add_argument('--threshold', type=int, default=3, help='Quality threshold for refinement.')
    p_refinev2.set_defaults(func=run_refinev2)

    p_fix = subparsers.add_parser('fix', help='Repairs technically broken (fuzzy) translations using an AI fixer.')
    p_fix.add_argument('--lang', help='Fix translations only for a specific language.')
    p_fix.set_defaults(func=run_fix)

    p_add_lang = subparsers.add_parser('add-lang', help='Adds a new language directory and .po file.')
    p_add_lang.add_argument('lang_code', help='The language code to add (e.g., "el" for Greek).')
    p_add_lang.set_defaults(func=run_add_lang)

    p_issues = subparsers.add_parser('issues', help='Shows a detailed breakdown of issues for a specific language.')
    p_issues.add_argument('lang_code', help='The language code to analyze (e.g., "es").')
    p_issues.set_defaults(func=run_issues)

    p_fix_interactive = subparsers.add_parser('fix-interactive', help='Launch an interactive session to fix strings for a language.')
    p_fix_interactive.add_argument('lang_code', help='The language code to fix.')
    p_fix_interactive.add_argument('--review-all', action='store_true', help='Review all strings, not just problematic ones.')
    p_fix_interactive.set_defaults(func=run_fix_interactive)
    
    p_test = subparsers.add_parser('test', help='Shows the command to test a language translation.')
    p_test.add_argument('lang_code', help='The language code to test (e.g., "es").')
    p_test.set_defaults(func=run_test)

    p_interactive = subparsers.add_parser('interactive', help='Launch the interactive expert-mode helper.')
    p_interactive.set_defaults(func=run_interactive)

    # NOW PARSE THE ARGUMENTS
    args = parser.parse_args()
    args.func(args)


# ======================================================================
# Core Pipeline Functions
# ======================================================================
# Update the do_refactor function in cli.py to use this:
def do_refactor(dry_run=False, no_backup=False):
    """Core refactor logic with smart directory detection."""
    project_root = get_project_root()
    print(f"📂 Project root: {project_root}")
    
    source_dirs = detect_source_directories(project_root)
    
    if not source_dirs:
        print("❌ No Python packages found!")
        return

    # STEP 1: Fix Underscore Placeholders
    print("\n🔧 [Step 1/3] Fixing lazy '_' placeholders to prevent shadowing...")
    if dry_run:
        print("   -> [DRY RUN] Would replace lazy '_' variables with 'unused'")
    else:
        try:
            underscore_fixer.auto_fix_all('_', 'unused')
        except Exception as e:
            print(f"   ❌ Error during underscore fixing: {e}")

    # STEP 2: F-String Conversion
    print("\n🔧 [Step 2/3] Converting f-strings to translation calls...")
    if dry_run:
        print("🔧 [DRY RUN] Would run: AST Code Refactor")
        converter = ASTFStringConverter(source_dirs=source_dirs, dry_run=True, backup=not no_backup)
        converter.scan_and_convert()
        converter.generate_report()
    else:
        print("🔧 Running: AST Code Refactor")
        converter = ASTFStringConverter(source_dirs=source_dirs, dry_run=False, backup=not no_backup)
        converter.scan_and_convert()
        converter.generate_report()

    # STEP 3: Inject Imports
    print("\n🔧 [Step 3/3] Injecting missing i18n imports...")
    if dry_run:
         print("   -> [DRY RUN] Would inject 'from omnipkg.i18n import _'")
    else:
        try:
             underscore_fixer.auto_fix_missing_imports()
        except Exception as e:
            print(f"   ❌ Error during import injection: {e}")
            
    print("\n✅ Refactor complete.")

def do_extract(dry_run=False):
    """Core extraction logic with dry-run support."""
    if dry_run:
        print("📤 [DRY RUN] Would run: String Extraction")
        print("   -> Would extract strings to .pot template")
        print("   -> Would update all .po files")
        return
        
    print("📤 Running: String Extraction")
    extract_strings.main()

def do_translate(target_lang=None, dry_run=False, use_ai=False, non_interactive=False):
    """Core translation logic that now correctly manages GPU resources for the AI."""
    if dry_run:
        lang_text = target_lang or "all languages"
        print(f"🌍 [DRY RUN] Would run: Auto-Translation for {lang_text} with AI={'enabled' if use_ai else 'disabled'}")
        if use_ai:
            print(" -> Would pause mining operations.")
            print(" -> Would run AI translation.")
            print(" -> Would resume mining operations.")
        return

    if use_ai:
        prepare_for_ai_task()
    
    try:
        print("🌍 Running: Auto-Translation")
        helper = TranslationHelper()
        
        if target_lang:
            if target_lang in helper.languages:
                print(f"-> Targeting single language: {target_lang}")
                helper.batch_auto_translate(target_lang, use_ai=use_ai, non_interactive=non_interactive)
            else:
                print(f"❌ Error: Language '{target_lang}' not found.")
        else:
            print("-> Targeting all available languages.")
            LANGUAGES_TO_EXCLUDE = ['en']
            languages_to_process = [lang for lang in helper.languages.keys() if lang not in LANGUAGES_TO_EXCLUDE]
            
            for i, lang_code in enumerate(languages_to_process):
                lang_name = helper.languages.get(lang_code, lang_code)
                print("\n" + "="*50 + f"\nProcessing language {i+1}/{len(languages_to_process)}: {lang_name} ({lang_code})\n" + "="*50)
                helper.batch_auto_translate(lang_code, use_ai=use_ai, non_interactive=non_interactive)
                
    finally:
        if use_ai:
            resume_mining_operations()

def do_compile(dry_run=False):
    """Core compilation logic with dry-run support."""
    if dry_run:
        print("🔨 [DRY RUN] Would run: Translation Compiler")
        print("   -> Would compile all .po files to .mo files")
        return
        
    print("🔨 Running: Translation Compiler")
    extract_strings.compile_all()

# ======================================================================
# CLI Wrapper Functions
# ======================================================================
def run_refactor(args):
    do_refactor(dry_run=args.dry_run, no_backup=args.no_backup)
    if args.dry_run:
        print("✅ Refactor dry-run complete.")
    else:
        print("✅ Refactor complete.")

def run_extract(args):
    do_extract(dry_run=getattr(args, 'dry_run', False))
    if getattr(args, 'dry_run', False):
        print("✅ Extraction dry-run complete.")
    else:
        print("✅ Extraction complete.")

def run_translate(args):
    do_translate(target_lang=args.lang, dry_run=getattr(args, 'dry_run', False), use_ai=getattr(args, 'ai', False))
    if getattr(args, 'dry_run', False):
        print("✅ Translation dry-run complete.")
    else:
        print("✅ Translation complete.")

def run_fix(args):
    """Finds all 'fuzzy' translations and uses the AI Fixer to repair them."""
    print("🛠️  Running: AI-Powered Technical Fixer")
    prepare_for_ai_task()
    try:
        helper = TranslationHelper()
        helper.fix_technical_errors(target_lang=args.lang)
    finally:
        resume_mining_operations()
    print("✅ Technical Fixer complete.")

def run_fix_interactive(args):
    """Launches the interactive fix session (original functionality)."""
    print(f"🛠️ Launching interactive fix session for {args.lang_code}")
    helper = TranslationHelper()
    helper.interactive_fix_session(args.lang_code, review_all=getattr(args, 'review_all', False))
    print("✅ Interactive fix session complete.")

def run_compile(args):
    do_compile(dry_run=getattr(args, 'dry_run', False))
    if getattr(args, 'dry_run', False):
        print("✅ Compilation dry-run complete.")
    else:
        print("✅ Compilation complete.")

def run_rescan(args):
    """Handles the logic for the 'rescan' command."""
    print("🔬 Running: AI-Powered Translation Rescan & Refinement")
    if args.lang:
        print(f"-> Targeting single language: {args.lang}")
    else:
        print("-> Targeting all available languages.")
    
    prepare_for_ai_task()
    try:
        helper = TranslationHelper()
        helper.rescan_and_fix(target_lang=args.lang)
    finally:
        resume_mining_operations()
    print("✅ Rescan and Refinement complete.")

def run_status(args):
    """Wrapper function that calls the helper to show the translation status."""
    helper = TranslationHelper()
    helper.show_status()

def run_pipeline(args):
    """Runs the full, orchestrated pipeline."""
    print("🚀 Running: Complete i18n Pipeline")
    if getattr(args, 'dry_run', False):
        print("🔍 DRY RUN MODE - No changes will be made")
    print("=" * 50)
    
    try:
        if not getattr(args, 'skip_refactor', False):
            print("\n[1/4] 🔧 REFACTOR PHASE")
            do_refactor(dry_run=getattr(args, 'dry_run', False), no_backup=False)
        else:
            print("\n[SKIPPED] 🔧 REFACTOR PHASE")
        
        print("\n[2/4] 📤 EXTRACTION PHASE")
        do_extract(dry_run=getattr(args, 'dry_run', False))
        
        if not getattr(args, 'skip_translate', False):
            print("\n[3/4] 🌍 TRANSLATION PHASE")
            do_translate(
                target_lang=args.lang, 
                dry_run=getattr(args, 'dry_run', False), 
                use_ai=getattr(args, 'ai', False), 
                non_interactive=True
            )
        else:
            print("\n[SKIPPED] 🌍 TRANSLATION PHASE")
        
        print("\n[4/4] 🔨 COMPILATION PHASE")
        do_compile(dry_run=getattr(args, 'dry_run', False))
        
        print("\n" + "=" * 50)
        if getattr(args, 'dry_run', False):
            print("🔍 DRY RUN COMPLETE - No changes were made")
        else:
            print("🎉 PIPELINE COMPLETE!")
            run_status(args)
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        print("🚨 Please check the error above and run individual commands to debug.")

def run_audit(args):
    """Handles the logic for the 'audit' command with improved, verbose output."""
    print("🔬 Running: AI-Powered Translation Audit")
    prepare_for_ai_task()
    try:
        auditor = TranslationAuditorEngine()
        helper = TranslationHelper()
        languages_to_audit = [args.lang] if args.lang else [lang for lang in helper.languages.keys() if lang != 'en']

        total_errors_found = 0
        for i, lang_code in enumerate(languages_to_audit):
            print("\n" + "="*70)
            print(f"🔬 Auditing language {i+1}/{len(languages_to_audit)}: {helper.languages.get(lang_code, lang_code)}")
            po_file = helper.get_po_file(lang_code)
            if not po_file: continue

            translated_entries = [e for e in po_file if e.msgid and e.msgstr]
            errors_in_file = 0
            
            for j, entry in enumerate(translated_entries):
                print(f"\n--- Auditing string {j+1}/{len(translated_entries)} ---")
                print(f"  - Original:    '{entry.msgid}'")
                print(f"  - Translation: '{entry.msgstr}'")
                
                if 'fuzzy' in entry.flags:
                    print("  - Status:      (Already flagged for review, skipping)")
                    continue

                print("  - AI Analysis: ", end='', flush=True)
                ai_decision = auditor.audit_string(entry.msgid, entry.msgstr, lang_code)

                if ai_decision and not ai_decision.get('is_valid', True):
                    errors_in_file += 1
                    total_errors_found += 1
                    print(f"🚨 BROKEN!")
                    print(f"    - AI Reasoning: {ai_decision.get('reasoning', 'N/A')}")
                    entry.flags.append('fuzzy')
                elif ai_decision and ai_decision.get('is_valid'):
                    print("✅ OK")
                    print(f"    - AI Reasoning: {ai_decision.get('reasoning', 'N/A')}")
                else:
                    print("⚠️ AI FAILED TO RESPOND")
            
            if errors_in_file > 0:
                print(f"\n💾 Found and flagged {errors_in_file} broken translations. Saving changes to {lang_code}.po")
                po_file.save()
            else:
                print("\n✅ No new broken translations found in this file.")
    finally:
        resume_mining_operations()
    
    print("\n" + "="*70)
    print(f"✅ Audit complete. Found a total of {total_errors_found} new issues across all scanned languages.")
    if total_errors_found > 0:
        print("💡 You can now run the 'rescan' command to send these flagged strings to the translation AI for fixing.")

def run_quality_audit(args):
    """Handles the logic for the 'quality-audit' command."""
    print("🔬 Running: AI-Powered Linguistic Quality Audit")
    prepare_for_ai_task()
    try:
        llama_path = "/home/minds3t/stealth/llama.cpp/build_cuda/bin/llama-cli"
        # NOTE: Use the model best suited for quality auditing. CodeLlama is okay, but a chat/instruct model might be better.
        model_path = "/ai_data/lollama/.lollama/blobs/codellama-13b-instruct.Q4_K_M.gguf"

        quality_auditor = TranslationQualityAuditorEngine(
            llama_cli_path=llama_path,
            model_path=model_path
        )
        helper = TranslationHelper()
        languages_to_audit = [args.lang] if args.lang else [lang for lang in helper.languages.keys() if lang != 'en']

        for i, lang_code in enumerate(languages_to_audit):
            print("\n" + "="*70)
            print(f"🔬 Quality Auditing language {i+1}/{len(languages_to_audit)}: {helper.languages.get(lang_code, lang_code)}")
            po_file = helper.get_po_file(lang_code)
            if not po_file: continue

            translations_to_audit = {
                entry.msgid: {"original": entry.msgid, "translation": entry.msgstr}
                for entry in po_file if entry.msgid and entry.msgstr
            }

            if not translations_to_audit:
                print("✅ No translated strings to audit in this file.")
                continue

            audit_results = quality_auditor.batch_audit_quality(translations_to_audit, lang_code)
            report = quality_auditor.generate_quality_report(audit_results, lang_code)
            print(report)

    finally:
        resume_mining_operations()
    
    print("\n✅ Linguistic Quality Audit complete.")

def run_add_lang(args):
    """Handles the logic for the 'add-lang' command."""
    print(f"➕ Running: Add Language '{args.lang_code}'")
    helper = TranslationHelper()
    helper.add_new_language(args.lang_code)
    print(f"✅ Add Language complete.")

def run_refinev2(args):
    """The ultimate refinement workflow."""
    print("✨ Running: Ultimate AI Translation Refinement")
    prepare_for_ai_task()
    try:
        helper = TranslationHelper()
        helper.refine_low_quality_translations_v2(
            target_lang=args.lang, 
            threshold=getattr(args, 'threshold', 3)
        )
    finally:
        resume_mining_operations()
    print("✅ Refinement complete.")

def run_refine(args):
    """Finds low-quality translations and uses the Refiner AI to generate a better version."""
    print("✨ Running: AI-Powered Translation Refinement")
    prepare_for_ai_task()
    try:
        quality_auditor = TranslationQualityAuditorEngine()
        refiner = TranslationRefinerEngine()
        helper = TranslationHelper()
        languages_to_process = [args.lang] if args.lang else [lang for lang in helper.languages.keys() if lang != 'en']

        total_refined = 0
        for i, lang_code in enumerate(languages_to_process):
            print("\n" + "="*70)
            print(f"✨ Refining language {i+1}/{len(languages_to_process)}: {helper.languages.get(lang_code, lang_code)}")
            po_file = helper.get_po_file(lang_code)
            if not po_file: continue

            translations_to_audit = {
                entry.msgid: {"original": entry.msgid, "translation": entry.msgstr}
                for entry in po_file if entry.msgid and entry.msgstr
            }
            if not translations_to_audit: continue

            print("--- Stage 1: Auditing for low-quality strings ---")
            audit_results = quality_auditor.batch_audit_quality(translations_to_audit, lang_code)
            
            print("\n--- Stage 2: Refining low-scoring translations ---")
            strings_to_refine = {
                msgid: result for msgid, result in audit_results.items() 
                if result['quality_score'] <= args.threshold
            }

            if not strings_to_refine:
                print("✅ No low-quality strings found to refine in this file.")
                continue

            refined_in_file = 0
            for msgid, result in strings_to_refine.items():
                print(f"\n- Refining string: '{result['original'][:50]}...'")
                print(f"  - Poor translation (Score {result['quality_score']}/5): '{result['translation'][:50]}...'")
                
                refinement_result = refiner.refine_translation(
                    result['original'], 
                    result['translation'], 
                    lang_code
                )
                
                if refinement_result and 'improved_translation' in refinement_result:
                    improved_text = refinement_result['improved_translation']
                    print(f"  - ✨ AI Suggestion: '{improved_text[:50]}...'")
                    
                    entry_to_update = po_file.find(msgid)
                    if entry_to_update:
                        entry_to_update.msgstr = improved_text
                        if 'fuzzy' in entry_to_update.flags:
                            entry_to_update.flags.remove('fuzzy')
                        refined_in_file += 1
                        print("    - ✅ Updated and saved.")
                else:
                    print("    - ⚠️ AI failed to provide a refinement.")
            
            if refined_in_file > 0:
                total_refined += refined_in_file
                print(f"\n💾 Refined and saved {refined_in_file} strings in {lang_code}.po")
                po_file.save()
            else:
                print("\n✅ No strings were successfully refined in this file.")

    finally:
        resume_mining_operations()
    
    print("\n" + "="*70)
    print(f"✅ Refinement complete. Improved a total of {total_refined} translations.")

def run_issues(args):
    """Handles the logic for the 'issues' command."""
    print(f"🔍 Running: Detailed Issues Analysis for '{args.lang_code}'")
    helper = TranslationHelper()
    helper.show_detailed_issues(args.lang_code)
    print(f"✅ Issues Analysis complete.")

def fix_technical_errors(self, target_lang: Optional[str] = None):
    """Orchestrates the process of fixing technically broken translations."""
    languages_to_process = [target_lang] if target_lang else [lang for lang in self.languages.keys() if lang != 'en']
    ai_fixer = TranslationFixerEngine()

    for lang_code in languages_to_process:
        print(f"\n--- Fixing technically broken translations for {lang_code} ---")
        po_file = self.get_po_file(lang_code)
        if not po_file: continue

        fuzzy_entries = []
        for entry in po_file:
            if 'fuzzy' in entry.flags:
                is_valid, reason = self._validate_translation(entry.msgid, entry.msgstr)
                fuzzy_entries.append((entry, reason))
        
        if not fuzzy_entries:
            print("    ✅ No translations flagged for fixing.")
            continue
        
        print(f"    - Found {len(fuzzy_entries)} translations to fix.")
        fixed_count = 0
        for i, (entry, reason) in enumerate(fuzzy_entries):
            print(f"    - Fixing string {i+1}/{len(fuzzy_entries)} ({reason}): '{entry.msgstr[:50]}...'")
            
            success, fix_reason, fix_result = ai_fixer.fix_translation(entry.msgid, entry.msgstr, lang_code, reason)
            
            if success and 'fixed_translation' in fix_result:
                fixed_text = fix_result['fixed_translation']
                if self._validate_translation(entry.msgid, fixed_text)[0]:
                    print(f"      - ✨ AI Fix successful ({fix_reason}): '{fixed_text[:50]}...'")
                    entry.msgstr = fixed_text
                    entry.flags.remove('fuzzy')
                    fixed_count += 1
                else:
                    print(f"      - ⚠️ AI Fix FAILED validation ({fix_reason}). Leaving as fuzzy.")
            else:
                print(f"      - ⚠️ AI failed to provide a fix ({fix_reason}). Leaving as fuzzy.")
        
        if fixed_count > 0:
            print(f"    💾 Repaired and saved {fixed_count} translations.")
            po_file.save()

def run_switch_project(args):
    """Prompts for a new project path and saves it to config."""
    config_path = get_config_path()
    
    print("\n📂 Current project configuration")
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                print(f"   Configured path: {config.get('project_root', 'None')}")
        except:
            print("   No valid config found")
    
    print("\nEnter the full path to your project directory:")
    print("(The directory that contains 'src/' or 'pyproject.toml')")
    
    new_path = input("\nProject path: ").strip()
    
    if not new_path:
        print("❌ Cancelled. No changes made.")
        return
    
    candidate = Path(new_path).expanduser().resolve()
    
    if not candidate.exists():
        print(f"❌ Path does not exist: {candidate}")
        return
    
    # Save it
    try:
        with open(config_path, 'w') as f:
            json.dump({'project_root': str(candidate)}, f, indent=4)
        print(f"\n✅ Project configured: {candidate}")
        print(f"   Config saved to: {config_path}")
        print(f"\n🔄 Restart")
    except Exception as e:
        print(f"❌ Failed to save config: {e}")

def run_test(args):
    """Handles the logic for the 'test' command."""
    print("📋 To test this language, run the following command in your terminal:")
    print("-" * 50)
    print(f"    LANG={args.lang_code} omnipkg --help")
    print("-" * 50)

def run_interactive(args):
    """Launches the full interactive menu from omnilang.core.helper.py."""
    print("🚀 Launching Interactive Expert Mode...")
    translation_helper_main()
    print("👋 Exited Interactive Expert Mode.")

# ======================================================================
# Smart Context & Configuration Loader
# ======================================================================

def get_config_path():
    """Returns a user-writable path for the config file."""
    config_home = os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')
    config_dir = Path(config_home) / 'omnilang'
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / 'i18n_config.json'

def load_or_create_config(tools_dir):
    """Loads project config from JSON or interactively creates it."""
    config_path = get_config_path()
    
    # 1. Try to load existing config
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                project_path = Path(config.get('project_root', '')).resolve()
                if project_path.exists():
                    return project_path
                print(f"⚠️  Configured path not found: {project_path}")
        except Exception as e:
            print(f"⚠️  Corrupt config file: {e}")

    # 2. If no valid config, Interactive Setup
    print("\n👋 Welcome to OmniPkg i18n Tools!")
    print("-----------------------------------")
    print("Please select the root directory of the project you want to translate.")
    print(f"Current Directory: {get_project_root()}")
    
    while True:
        user_input = input(f"\nEnter Project Path [default: {get_project_root().parent}]: ").strip()
        
        # Default to parent of tools dir (standard structure)
        if not user_input:
            candidate = tools_dir.parent
        else:
            candidate = Path(user_input).expanduser().resolve()
        
        # Validate structure (look for src, pyproject.toml, or .git to confirm it's a repo)
        if not candidate.exists():
            print(f"❌ Path does not exist: {candidate}")
            continue
            
        # Check for source code indicators
        has_src = (candidate / 'src').exists()
        has_toml = (candidate / 'pyproject.toml').exists()
        
        if not (has_src or has_toml):
            confirm = input(f"⚠️  Warning: No 'src' folder or 'pyproject.toml' found in {candidate.name}. Is this correct? (y/n): ")
            if confirm.lower() not in ['y', 'yes']:
                continue

        # Save the valid config
        try:
            with open(config_path, 'w') as f:
                json.dump({'project_root': str(candidate)}, f, indent=4)
            print(f"✅ Configuration saved to: {config_path}")
            return candidate
        except Exception as e:
            print(f"❌ Failed to save config: {e}")
            sys.exit(1)

if __name__ == '__main__':
    # 1. Locate the tools directory (where this script lives)
    tools_dir = Path(__file__).parent.resolve()
    
    # 2. Load the target project root (from config or user input)
    project_root = load_or_create_config(tools_dir)
    
    # 3. Inject Paths for Imports
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))

    src_path = project_root / 'src'
    if src_path.exists():
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))
    elif str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # 4. Change Working Directory
    os.chdir(project_root)

    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Execution interrupted by user.")
        sys.exit(0)