#!/usr/bin/env python3
"""
Enhanced i18n script for omnipkg with diagnostic capabilities.
Uses centralized path detection from common_utils.
"""
import subprocess
import sys
import os
from pathlib import Path
import polib
import re
import gettext

# Import our centralized utilities
from omnilang.common_utils import (
    get_project_root,
    detect_source_directories,
    detect_locale_directory,
    detect_pot_file
)

# --- Path Configuration (NOW USING COMMON UTILS!) ---
PROJECT_ROOT = get_project_root()
SOURCE_DIRS = detect_source_directories(PROJECT_ROOT)
LOCALE_DIR = detect_locale_directory(PROJECT_ROOT)
POT_FILE = detect_pot_file(PROJECT_ROOT, LOCALE_DIR)
DOMAIN = POT_FILE.stem  # Extract domain from pot filename

def extract_strings():
    """Step 1: Scan source code and create/update the master .pot template file."""
    print("--- Step 1: Extracting translatable strings ---")
    print(f"Project Root: {PROJECT_ROOT}")
    print(f"Source Directories: {[str(d) for d in SOURCE_DIRS]}")
    print(f"Target Locale Directory: {LOCALE_DIR}")
    print(f"POT File: {POT_FILE}")
    
    # Ensure locale directory exists
    LOCALE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Build list of all Python files from source directories
        python_files = []
        for source_dir in SOURCE_DIRS:
            if source_dir.exists():
                python_files.extend([str(f) for f in source_dir.rglob('*.py')])
        
        if not python_files:
            print("⚠️  No Python files found in source directories!")
            return False
        
        print(f"Found {len(python_files)} Python files to scan")
        
        # Run pygettext3 with individual files instead of directories
        command = ['pygettext3', '-d', DOMAIN, '-o', str(POT_FILE)] + python_files
        
        print(f"Running: pygettext3 -d {DOMAIN} -o {POT_FILE} <{len(python_files)} files>")
        
        result = subprocess.run(
            command,
            check=True, 
            capture_output=True, 
            text=True, 
            cwd=PROJECT_ROOT
        )
        
        print(f"✓ Master template updated: {POT_FILE}")
        
        if POT_FILE.exists():
            pot = polib.pofile(str(POT_FILE))
            print(f"  📊 Found {len(pot)} translatable strings in source code.")
        
        return True
        
    except FileNotFoundError:
        print("❌ Error: 'pygettext3' command not found.")
        print("   Install with: sudo apt-get install python3-gettext")
        return False
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error during string extraction:")
        print(f"   {e.stderr}")
        return False

def find_hardcoded_spanish():
    """Diagnostic: Find potentially hardcoded Spanish strings in the source code."""
    print("\n🔍 Scanning for potentially hardcoded Spanish strings...")
    
    spanish_patterns = [
        r'(Realizar|realizar)',
        r'(sincronización|sincronizacion)',
        r'(autocuración|autocuracion)',
        r'(conocimiento)',
        r'(entorno|ambiente)',
        r'(Estado|estado)',
        r'(sistema)',
        r'(cárcel|carcel)',
        r'(burbujas)',
    ]
    
    found_issues = []
    
    for source_dir in SOURCE_DIRS:
        if not source_dir.exists():
            continue
            
        for py_file in source_dir.rglob('*.py'):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    line_num = 0
                    for line in content.split('\n'):
                        line_num += 1
                        for pattern in spanish_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                found_issues.append({
                                    'file': py_file,
                                    'line': line_num,
                                    'content': line.strip(),
                                    'pattern': pattern
                                })
            except Exception as e:
                print(f"  ⚠️  Could not scan {py_file}: {e}")
    
    if found_issues:
        print(f"  🚨 Found {len(found_issues)} potential hardcoded strings:")
        for issue in found_issues[:10]:
            print(f"    {issue['file']}:{issue['line']} -> {issue['content']}")
    else:
        print("  ✅ No obvious hardcoded Spanish strings found")

def check_mo_file_integrity():
    """Diagnostic: Check if .mo files are properly compiled and readable."""
    print("\n🔍 Checking .mo file integrity...")
    
    for mo_path in LOCALE_DIR.glob('**/LC_MESSAGES/*.mo'):
        lang = mo_path.parent.parent.name
        try:
            with open(mo_path, 'rb') as f:
                data = f.read()
                if len(data) < 28:
                    print(f"  ❌ {lang}: .mo file too small ({len(data)} bytes)")
                    continue
                
                magic = data[:4]
                if magic not in [b'\xde\x12\x04\x95', b'\x95\x04\x12\xde']:
                    print(f"  ❌ {lang}: Invalid .mo file magic number")
                    continue
            
            try:
                translation = gettext.translation(DOMAIN, LOCALE_DIR, languages=[lang])
                test_msg = "Install packages with intelligent conflict resolution"
                translated = translation.gettext(test_msg)
                
                if translated == test_msg:
                    print(f"  ⚠️  {lang}: .mo file loads but test string not translated")
                else:
                    print(f"  ✅ {lang}: .mo file OK - test translation: '{translated[:50]}...'")
                    
            except Exception as e:
                print(f"  ❌ {lang}: Cannot load with gettext: {e}")
                
        except Exception as e:
            print(f"  ❌ {lang}: Cannot read .mo file: {e}")

def compare_po_vs_mo():
    """Diagnostic: Compare .po and .mo files to ensure they match."""
    print("\n🔍 Comparing .po vs .mo file consistency...")
    
    for po_path in LOCALE_DIR.glob('**/LC_MESSAGES/*.po'):
        lang = po_path.parent.parent.name
        mo_path = po_path.with_suffix('.mo')
        
        if not mo_path.exists():
            print(f"  ❌ {lang}: .mo file missing")
            continue
        
        try:
            po_mtime = po_path.stat().st_mtime
            mo_mtime = mo_path.stat().st_mtime
            
            if po_mtime > mo_mtime:
                print(f"  ⚠️  {lang}: .po file newer than .mo file - recompilation needed")
            else:
                print(f"  ✅ {lang}: .mo file up to date")
                
            po = polib.pofile(str(po_path))
            translated_count = len([e for e in po if e.msgstr and not e.obsolete])
            print(f"    📊 {translated_count} translations in .po file")
            
        except Exception as e:
            print(f"  ❌ {lang}: Error comparing files: {e}")

def clear_gettext_cache():
    """Clear Python's gettext cache to ensure fresh translations are loaded."""
    print("\n🧹 Clearing gettext cache...")
    
    if hasattr(gettext, '_translations'):
        gettext._translations.clear()
        print("  ✅ Gettext translation cache cleared")
    
    import sys
    for module_name, module in list(sys.modules.items()):
        # Only inspect modules belonging to our application
        if not module_name.startswith(DOMAIN):
            continue

        if hasattr(module, '_') and hasattr(module._, 'gettext'):
            try:
                if hasattr(module._, '_catalog'):
                    module._._catalog.clear()
                    print(f"  ✅ Cleared cache for module: {module_name}")
            except:
                pass

def test_runtime_translation(lang_code='es'):
    """Test translation loading at runtime."""
    print(f"\n🧪 Testing runtime translation for '{lang_code}'...")
    
    try:
        os.environ['LANG'] = lang_code
        os.environ['LANGUAGE'] = lang_code
        
        translation = gettext.translation(DOMAIN, LOCALE_DIR, languages=[lang_code], fallback=True)
        
        test_strings = [
            "Install packages with intelligent conflict resolution",
            "The intelligent Python package manager that eliminates dependency hell",
            "Available commands:",
            "Multi-version environment health dashboard"
        ]
        
        print(f"  Testing {len(test_strings)} strings:")
        for test_str in test_strings:
            translated = translation.gettext(test_str)
            status = "✅" if translated != test_str else "❌"
            print(f"    {status} '{test_str[:40]}...' -> '{translated[:40]}...'")
            
    except Exception as e:
        print(f"  ❌ Error testing translation: {e}")

def update_po_files():
    """Step 2: Update all language-specific .po files AND prune obsolete entries."""
    print("\n--- Step 2: Updating & Pruning language files (.po) ---")
    
    if not POT_FILE.exists():
        print("❌ Master template (.pot) not found. Cannot update. Run extraction first.")
        return

    pot = polib.pofile(str(POT_FILE))
    print(f"  📊 Master template contains {len(pot)} strings.")
    
    po_files_to_process = list(LOCALE_DIR.glob('**/LC_MESSAGES/*.po'))
    print(f"  🔍 Found {len(po_files_to_process)} language files to process.")

    for po_path in po_files_to_process:
        lang = po_path.parent.parent.name
        try:
            po = polib.pofile(str(po_path))
            
            obsolete_before = len(po.obsolete_entries())
            po.merge(pot)
            
            obsolete_entries = po.obsolete_entries()
            if obsolete_entries:
                print(f"  -> Pruning {len(obsolete_entries)} obsolete translations from {lang}...")
                for entry in list(obsolete_entries):
                    po.remove(entry)
            
            po.save(str(po_path))
            
            total_count = len([e for e in po if e.msgid and not e.obsolete])
            translated_count = len([e for e in po if e.msgstr and not e.obsolete])
            
            print(f"  -> Updated {lang}: {translated_count}/{total_count} translated ({translated_count/total_count*100:.1f}%)")
        except Exception as e:
            print(f"  ❌ Failed to update {lang}: {e}")
    
    print(f"✓ Processed {len(po_files_to_process)} language files.")

def compile_all():
    """Step 3: Compile all .po text files into .mo binary files for the application to use."""
    print("\n--- Step 3: Compiling all translations to binary (.mo) files ---")
    compiled_count = 0
    
    for po_path in LOCALE_DIR.glob('**/LC_MESSAGES/*.po'):
        lang = po_path.parent.parent.name
        mo_path = po_path.with_suffix('.mo')
        try:
            po = polib.pofile(str(po_path))
            translated_count = len([e for e in po if e.msgstr and not e.obsolete])
            total_count = len([e for e in po if e.msgid and not e.obsolete])
            
            po.save_as_mofile(str(mo_path))
            
            mo_size = mo_path.stat().st_size
            print(f"  ✓ Compiled {lang}: {translated_count}/{total_count} strings -> {mo_size} bytes")
            compiled_count += 1
        except Exception as e:
            print(f"  ❌ Failed to compile {lang}: {e}")
    
    print(f"\n✓ Compilation complete. Processed {compiled_count} files.")
    clear_gettext_cache()

def run_diagnostics():
    """Run comprehensive diagnostics to identify translation issues."""
    print("🔬 Running comprehensive translation diagnostics...\n")
    
    find_hardcoded_spanish()
    check_mo_file_integrity()
    compare_po_vs_mo()
    clear_gettext_cache()
    test_runtime_translation('es')
    
    print("\n📋 Diagnostic Summary:")
    print("  1. Check above for any hardcoded Spanish strings in source code")
    print("  2. Verify .mo files are properly compiled and readable")
    print("  3. Ensure .po and .mo files are in sync")
    print("  4. Test runtime translation loading")
    print("\n💡 If issues persist, the problem might be:")
    print("  - Application code not using gettext properly")
    print("  - Wrong locale directory path in application") 
    print("  - Cached translations in the application")
    print("  - Mixed hardcoded and translated strings")

def main():
    """Main function to handle command line arguments and run the workflow."""
    print(f"🔧 Configuration:")
    print(f"   Project Root: {PROJECT_ROOT}")
    print(f"   Source Dirs: {[str(d.relative_to(PROJECT_ROOT)) for d in SOURCE_DIRS]}")
    print(f"   Locale Dir: {LOCALE_DIR.relative_to(PROJECT_ROOT)}")
    print(f"   Domain: {DOMAIN}\n")
    
    if '--compile-only' in sys.argv:
        compile_all()
    elif '--extract-only' in sys.argv:
        extract_strings()
    elif '--diagnostics' in sys.argv:
        run_diagnostics()
    elif '--test-lang' in sys.argv:
        lang = sys.argv[sys.argv.index('--test-lang') + 1] if len(sys.argv) > sys.argv.index('--test-lang') + 1 else 'es'
        test_runtime_translation(lang)
    else:
        print("Starting full i18n update process...")
        if extract_strings():
            update_po_files()
            compile_all()
            print("\n✅ Full translation update complete!")
            
            print("\n🔍 Running quick diagnostics...")
            check_mo_file_integrity()
        else:
            print("\n❌ Process failed during string extraction.")

if __name__ == '__main__':
    try:
        import polib
    except ImportError:
        print("❌ Error: 'polib' library not found.")
        print("   Please install it by running: pip install polib")
        sys.exit(1)
    
    if '--help' in sys.argv:
        print("Usage:")
        print("  python extract_strings.py                 # Full extraction, update, and compilation")
        print("  python extract_strings.py --extract-only  # Only extract strings to .pot")
        print("  python extract_strings.py --compile-only  # Only compile .po to .mo files")
        print("  python extract_strings.py --diagnostics   # Run comprehensive diagnostics")
        print("  python extract_strings.py --test-lang es  # Test translation loading for specific language")
        sys.exit(0)
        
    main()