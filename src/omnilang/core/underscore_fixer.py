#!/usr/bin/env python3
"""
Find and fix standalone underscore (_) variables that aren't i18n translation calls.

GOAL: Replace lazy placeholder _ variables while preserving translation function calls.

Strategy:
1. Skip variable assignments/usage that are actual _('text') translation calls
2. Fix lazy placeholders: for _, x in items | x, _, y = values
3. Auto-fix: Automatically inject 'from omnipkg.i18n import _' into files using translations without import
"""

import re
import os
import sys
import json
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Set

# Configuration
IGNORE_LIST_FILE = ".underscore_ignore.json"

# Exclude dev tools directory entirely and other artifacts
EXCLUDED_DIRS = {
    '.git', '__pycache__', 'venv', '.venv', 'node_modules',
    '.tox', 'build', 'dist', '.eggs', 'dev_tools', '_vendor'
}

# Files that define the i18n system itself OR are dev tools
PROTECTED_FILES = [
    'i18n.py',
    '__main__.py',
]

PROTECTED_PATTERNS = [
    r'/i18n\.py',
    r'/__main__\.py',
]

def safe_print(*args, **kwargs):
    """Fallback safe_print."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        print(*[str(arg).encode('ascii', 'ignore').decode() for arg in args], **kwargs)

def is_protected_file(file_path: str) -> bool:
    """Check if this is a protected file (i18n definition, main, etc)."""
    basename = os.path.basename(file_path)
    if basename in PROTECTED_FILES:
        return True
    for pattern in PROTECTED_PATTERNS:
        if re.search(pattern, file_path):
            return True
    if 'underscore_fixer.py' in file_path:
        return True
    return False

def file_has_i18n_import(content: str) -> bool:
    """Check if the file imports _ for i18n translation."""
    patterns = [
        r'from\s+\.i18n\s+import\s+.*\b_\b',
        r'from\s+\w+\.i18n\s+import\s+.*\b_\b',
        r'from\s+omnipkg\.i18n\s+import\s+.*\b_\b',
        r'import\s+gettext',
        r'_\s*=\s*gettext\.',
        r'_\s*=\s*Translator\(',
    ]
    return any(re.search(pattern, content) for pattern in patterns)

def is_translation_call(line: str) -> bool:
    """Check if this line contains _('text') or _("text")."""
    return bool(re.search(r'\b_\s*\([\'"]', line))

def extract_translation_calls(content: str) -> List[int]:
    """Extract line numbers that contain translation function calls."""
    lines = content.splitlines()
    translation_lines = []
    for i, line in enumerate(lines, 1):
        if is_translation_call(line):
            translation_lines.append(i)
    return translation_lines

def find_variable_uses(file_path: str, target_var: str = '_') -> List[Tuple[int, str, str]]:
    """Find lazy placeholder uses of _ (NOT translation calls)."""
    if is_protected_file(file_path):
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.splitlines(keepends=True)
    except Exception as e:
        safe_print(f"Error reading {file_path}: {e}")
        return []
    
    translation_lines = set(extract_translation_calls(content))
    findings = []
    in_multiline_string = False
    string_delimiter = None
    
    for i, line in enumerate(lines, 1):
        if i in translation_lines: continue
        
        # Track multiline strings
        if '"""' in line or "'''" in line:
            if '"""' in line: delimiter = '"""'
            else: delimiter = "'''"
            count = line.count(delimiter)
            if not in_multiline_string:
                if count % 2 == 1:
                    in_multiline_string = True
                    string_delimiter = delimiter
            else:
                if delimiter == string_delimiter and count % 2 == 1:
                    in_multiline_string = False
                    string_delimiter = None
        if in_multiline_string: continue
        
        # Skip comments
        stripped = line.strip()
        if stripped.startswith('#'): continue
        
        # Skip if target_var appears only inside string literals
        temp_line = re.sub(r'"[^"]*"', '""', line)
        temp_line = re.sub(r"'[^']*'", "''", temp_line)
        temp_line = re.sub(r'f"[^"]*"', 'f""', temp_line)
        temp_line = re.sub(r"f'[^']*'", "f''", temp_line)
        
        if target_var not in temp_line: continue
        
        # Skip comprehensions
        if re.search(rf'[\[\{{]\s*.*\s+for\s+.*\b{target_var}\b', line): continue
        
        # Pattern 1: Simple assignment
        if re.search(rf'(^|\s){target_var}\s*=\s+', line):
            findings.append((i, line, 'assignment'))
            continue
        # Pattern 2: For loop variable
        if re.search(r'^\s*for\s+', line):
            for_match = re.search(r'^\s*for\s+(.*?)\s+in\s+', line)
            if for_match:
                var_list = for_match.group(1)
                if re.search(rf'\b{target_var}\b', var_list):
                    findings.append((i, line, 'for_loop'))
                    continue
        # Pattern 3: Tuple unpacking
        if re.search(rf',\s*{target_var}\s*[,=]', line):
            findings.append((i, line, 'tuple_unpack'))
            continue
        # Pattern 4: Function parameter
        if re.search(rf'\bdef\s+\w+\([^)]*\b{target_var}\b', line):
            findings.append((i, line, 'function_param'))
            continue
    
    return findings

def find_files_needing_i18n_import(directory: str = '.') -> List[str]:
    """Find files that use _('text') translation calls but don't have the i18n import."""
    files_needing_import = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                if is_protected_file(file_path): continue
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if extract_translation_calls(content) and not file_has_i18n_import(content):
                        files_needing_import.append(file_path)
                except: pass
    return files_needing_import

def inject_i18n_import(file_path: str) -> bool:
    """
    Automatically inject 'from omnipkg.i18n import _' at the TOP LEVEL.
    
    Safe Strategies:
    1. Ignores imports inside try/except, functions, or classes (by checking indentation).
    2. Ignores imports inside multi-line strings/docstrings.
    3. Inserts after the last TOP-LEVEL import found.
    4. If no top-level imports, inserts after shebang/encoding/docstrings.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 1. Determine safe starting point (skip shebang/encoding)
        start_scan = 0
        if len(lines) > 0 and (lines[0].startswith('#!') or 'coding:' in lines[0]):
            start_scan += 1
            if len(lines) > 1 and (lines[1].startswith('#!') or 'coding:' in lines[1]):
                start_scan += 1
                
        last_top_level_import_index = -1
        end_of_docstring_index = -1
        
        in_multiline = False
        delimiter = None
        
        for i in range(start_scan, len(lines)):
            line = lines[i]
            
            # --- State Machine for Strings ---
            if '"""' in line or "'''" in line:
                # Naive check: if line starts with delimiter, it's likely a docstring
                # This isn't a full tokenizer but works for standard module docstrings
                stripped = line.strip()
                curr_delim = '"""' if '"""' in line else "'''"
                
                # Count occurrences
                count = line.count(curr_delim)
                
                if not in_multiline:
                    # Start of string?
                    if count % 2 != 0:
                        in_multiline = True
                        delimiter = curr_delim
                    else:
                        # Single line docstring (e.g. """doc""")
                        # If this is at the top of the file, mark it
                        if last_top_level_import_index == -1:
                            end_of_docstring_index = i
                else:
                    # Closing string?
                    if curr_delim == delimiter and count % 2 != 0:
                        in_multiline = False
                        if last_top_level_import_index == -1:
                            end_of_docstring_index = i
            
            if in_multiline:
                continue
                
            # --- Check for Top-Level Imports ---
            # MUST start with 'import' or 'from' with NO indentation
            if line.startswith('import ') or line.startswith('from '):
                # Don't hook onto __future__ imports if we can help it, 
                # but technically they are top level.
                last_top_level_import_index = i
                
        # --- Determine Insertion Point ---
        if last_top_level_import_index != -1:
            # Insert after the last import
            insert_idx = last_top_level_import_index + 1
        else:
            # No top-level imports found.
            if end_of_docstring_index != -1:
                # Insert after docstring
                insert_idx = end_of_docstring_index + 1
            else:
                # Insert at start (after shebangs)
                insert_idx = start_scan

        # Prepare line
        import_line = "from omnipkg.i18n import _\n"
        
        # Check if we need to add a newline (if inserting into dense code)
        if insert_idx < len(lines) and lines[insert_idx].strip() != "":
            # Only if strictly necessary, but standard imports usually just stack
            pass

        lines.insert(insert_idx, import_line)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return True
    except Exception as e:
        safe_print(f"❌ Error fixing {file_path}: {e}")
        return False

def get_line_context(lines: List[str], line_num: int, context_lines: int = 2) -> str:
    start = max(0, line_num - context_lines - 1)
    end = min(len(lines), line_num + context_lines)
    context = []
    for i in range(start, end):
        prefix = ">>> " if i == line_num - 1 else "    "
        context.append(f"{prefix}{i+1:4d} | {lines[i].rstrip()}")
    return '\n'.join(context)

def get_function_context(lines: List[str], line_num: int) -> Tuple[str, int, int]:
    func_start = None
    indent_level = None
    for i in range(line_num - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith('def ') or stripped.startswith('async def '):
            func_start = i
            indent_level = len(lines[i]) - len(lines[i].lstrip())
            break
    if func_start is None:
        start = max(0, line_num - 10)
        end = min(len(lines), line_num + 10)
        return ''.join(lines[start:end]), start + 1, end
    decorator_start = func_start
    for i in range(func_start - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith('@'): decorator_start = i
        elif stripped and not stripped.startswith('#'): break
    func_start = decorator_start
    func_end = len(lines)
    for i in range(func_start + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('#'): continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= indent_level:
            func_end = i
            break
    return ''.join(lines[func_start:func_end]), func_start + 1, func_end

def suggest_replacement(line: str, context_type: str) -> List[str]:
    suggestions = ['unused', 'ignored', 'result']
    if context_type == 'tuple_unpack':
        if 'select.select' in line: suggestions = ['writable', 'exceptional', 'unused_writable', 'unused']
        elif 'return_code' in line.lower() or 'status' in line.lower(): suggestions = ['output', 'stdout', 'result', 'unused']
        elif 'value' in line.lower(): suggestions = ['val', 'value', 'item', 'unused']
        else: suggestions = ['value', 'item', 'data', 'unused']
    elif context_type == 'for_loop':
        if 'enumerate' in line: suggestions = ['idx', 'index', 'i', 'unused']
        elif 'items()' in line: suggestions = ['key', 'value', 'k', 'unused']
        else: suggestions = ['item', 'elem', 'value', 'unused']
    elif context_type == 'assignment':
        if 'select.select' in line: suggestions = ['writable', 'exceptional', 'unused_writable', 'unused']
        elif 'return' in line.lower() or 'status' in line.lower(): suggestions = ['output', 'result', 'status', 'unused']
        else: suggestions = ['result', 'value', 'data', 'unused']
    elif context_type == 'function_param': suggestions = ['unused', 'ignored', 'args', 'kwargs']
    return suggestions

def apply_fix(file_path: str, line_num: int, old_var: str, new_var_name: str) -> bool:
    try:
        with open(file_path, 'r', encoding='utf-8') as f: lines = f.readlines()
        old_content = lines[line_num - 1]
        new_content = re.sub(rf'\b{re.escape(old_var)}\b', new_var_name, old_content)
        if new_content == old_content:
            safe_print(f"⚠️  No changes made (pattern not found)")
            return False
        lines[line_num - 1] = new_content
        with open(file_path, 'w', encoding='utf-8') as f: f.writelines(lines)
        return True
    except Exception as e:
        safe_print(f"❌ Error applying fix: {e}")
        return False

def load_ignore_list() -> Dict:
    if os.path.exists(IGNORE_LIST_FILE):
        try:
            with open(IGNORE_LIST_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def save_ignore_list(ignore_list: Dict):
    with open(IGNORE_LIST_FILE, 'w') as f: json.dump(ignore_list, f, indent=2)

def add_to_ignore_list(file_path: str, line_num: int, line: str):
    ignore_list = load_ignore_list()
    key = f"{file_path}:{line_num}"
    ignore_list[key] = {'file': file_path, 'line': line_num, 'content': line.strip()}
    save_ignore_list(ignore_list)

def is_ignored(file_path: str, line_num: int) -> bool:
    ignore_list = load_ignore_list()
    key = f"{file_path}:{line_num}"
    return key in ignore_list

def review_ignore_list():
    ignore_list = load_ignore_list()
    if not ignore_list:
        safe_print("✅ No items in ignore list!")
        return
    items = list(ignore_list.items())
    safe_print(f"\n📋 IGNORE LIST ({len(items)} items)")
    safe_print("=" * 80)
    for idx, (key, item) in enumerate(items, 1):
        safe_print(f"{idx:3d}) {item['file']}:{item['line']}")
        safe_print(f"     {item['content'][:70]}")
    safe_print("\nOptions:")
    safe_print("  1-N: Review and fix item number")
    safe_print("  c: Clear entire ignore list")
    safe_print("  q: Back to main menu")
    while True:
        choice = input("\nYour choice: ").strip().lower()
        if choice == 'q': return
        elif choice == 'c':
            if input("Clear entire ignore list? (y/n): ").strip().lower() == 'y':
                save_ignore_list({})
                safe_print("✅ Ignore list cleared!")
                return
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                key, item = items[idx]
                file_path = item['file']
                line_num = item['line']
                try:
                    with open(file_path, 'r') as f: lines = f.readlines()
                    safe_print("\n" + "=" * 80)
                    safe_print(get_line_context(lines, line_num, context_lines=5))
                    safe_print("=" * 80)
                    new_name = input("Enter new variable name (or 's' to skip): ").strip()
                    if new_name and new_name != 's' and new_name.isidentifier():
                        if apply_fix(file_path, line_num, '_', new_name):
                            safe_print(f"✅ Fixed: _ → {new_name}")
                            del ignore_list[key]
                            save_ignore_list(ignore_list)
                            return review_ignore_list()
                except Exception as e: safe_print(f"❌ Error: {e}")
            else: safe_print(f"❌ Invalid choice. Pick 1-{len(items)}")

def auto_fix_all(target_var: str = '_', replacement: str = 'unused'):
    # USE CONFIG SYSTEM
    from omnilang.common_utils import get_project_root, detect_source_directories
    
    project_root = get_project_root()
    source_dirs = detect_source_directories(project_root)
    
    all_findings = []
    protected_count = 0
    
    for src_dir in source_dirs:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    if is_protected_file(file_path):
                        protected_count += 1
                        continue
                    findings = find_variable_uses(file_path, target_var)
                    if findings: all_findings.append((file_path, findings))
    if protected_count > 0: safe_print(f"🛡️  Protected {protected_count} file(s)")
    if not all_findings:
        safe_print(f"✅ No lazy placeholder '{target_var}' variables found!")
        return 0
    total = sum(len(findings) for _, findings in all_findings)
    safe_print(f"\n🔧 Auto-fixing {total} lazy placeholder '{target_var}' → '{replacement}'...")
    fixed_count = 0
    for file_path, findings in all_findings:
        for line_num, line, context_type in findings:
            if is_ignored(file_path, line_num): continue
            if apply_fix(file_path, line_num, target_var, replacement):
                safe_print(f"  ✓ {file_path}:{line_num} ({context_type})")
                fixed_count += 1
    safe_print(f"\n✅ Fixed {fixed_count} lazy placeholder(s)")
    return fixed_count

def auto_fix_missing_imports():
    safe_print("\n" + "=" * 80)
    safe_print("🌍 Auto-Fixing missing i18n imports...")
    safe_print("=" * 80)
    
    # Use the existing config system
    from omnilang.common_utils import get_project_root, detect_source_directories
    
    project_root = get_project_root()
    source_dirs = detect_source_directories(project_root)
    
    safe_print(f"🔍 Searching in: {project_root}")
    
    files_needing = []
    for src_dir in source_dirs:
        files_needing.extend(find_files_needing_i18n_import(str(src_dir)))
    
    if not files_needing:
        safe_print("\n✅ All files using _() already have the i18n import!")
        return
    
    safe_print(f"\n⚠️  Found {len(files_needing)} file(s) using _('text') without import.")
    safe_print(f"    Injecting 'from omnipkg.i18n import _'...\n")
    
    fixed_count = 0
    for file_path in files_needing:
        if inject_i18n_import(file_path):
            safe_print(f"  ✓ Injected import into {file_path}")
            fixed_count += 1
    
    safe_print(f"\n✅ Fixed {fixed_count} file(s)")

def interactive_mode(target_var: str = '_'):
    # USE CONFIG SYSTEM
    from omnilang.common_utils import get_project_root, detect_source_directories
    
    project_root = get_project_root()
    source_dirs = detect_source_directories(project_root)
    
    all_findings = []
    protected_count = 0
    
    for src_dir in source_dirs:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    if is_protected_file(file_path):
                        protected_count += 1
                        continue
                    findings = find_variable_uses(file_path, target_var)
                    if findings: all_findings.append((file_path, findings))
    if protected_count > 0: safe_print(f"\n🛡️  Protected {protected_count} file(s)")
    if not all_findings:
        safe_print(f"\n✅ No lazy placeholder '{target_var}' variables found!")
        return
    total = sum(len(findings) for _, findings in all_findings)
    safe_print(f"\n🔍 Found {total} lazy placeholder '{target_var}' in {len(all_findings)} file(s)")
    fixed_count = 0
    for file_path, findings in all_findings:
        try:
            with open(file_path, 'r', encoding='utf-8') as f: lines = f.readlines()
        except Exception as e:
            safe_print(f"❌ Error reading {file_path}: {e}")
            continue
        for line_num, line, context_type in findings:
            if is_ignored(file_path, line_num): continue
            safe_print("\n" + "=" * 80)
            safe_print(f"📁 {file_path}:{line_num}")
            safe_print(f"🔍 Context type: {context_type}")
            safe_print("=" * 80)
            safe_print("\n📋 Line context:")
            safe_print(get_line_context(lines, line_num))
            suggestions = suggest_replacement(line, context_type)
            safe_print(f"\n💡 Suggested replacements: {', '.join(suggestions)}")
            safe_print("\nOptions:")
            safe_print("  1-9: Use suggested name")
            safe_print("  f: Show full function")
            safe_print("  m: Show more context (10 lines)")
            safe_print("  c: Custom name")
            safe_print("  i: Ignore (add to ignore list)")
            safe_print("  s: Skip")
            safe_print("  q: Quit")
            while True:
                choice = input("\nYour choice: ").strip().lower()
                if choice == 'q':
                    safe_print(f"\n✅ Fixed {fixed_count} occurrence(s)")
                    return
                elif choice == 's':
                    safe_print("⏭️  Skipped")
                    break
                elif choice == 'i':
                    add_to_ignore_list(file_path, line_num, line)
                    safe_print("➕ Added to ignore list")
                    break
                elif choice == 'f':
                    func_text, start, end = get_function_context(lines, line_num)
                    safe_print("\n" + "─" * 80)
                    for i, l in enumerate(func_text.split('\n'), start):
                        prefix = ">>> " if i == line_num else "    "
                        safe_print(f"{prefix}{i:4d} | {l}")
                    safe_print("─" * 80)
                elif choice == 'm':
                    safe_print("\n" + "─" * 80)
                    safe_print(get_line_context(lines, line_num, context_lines=10))
                    safe_print("─" * 80)
                elif choice == 'c':
                    custom = input("Enter custom variable name: ").strip()
                    if custom and custom.isidentifier():
                        if apply_fix(file_path, line_num, target_var, custom):
                            safe_print(f"✅ Fixed: {target_var} → {custom}")
                            fixed_count += 1
                            with open(file_path, 'r') as f: lines = f.readlines()
                        break
                    else: safe_print("❌ Invalid identifier")
                elif choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(suggestions):
                        new_name = suggestions[idx]
                        if apply_fix(file_path, line_num, target_var, new_name):
                            safe_print(f"✅ Fixed: {target_var} → {new_name}")
                            fixed_count += 1
                            with open(file_path, 'r') as f: lines = f.readlines()
                        break
                    else: safe_print(f"❌ Invalid. Pick 1-{len(suggestions)}")
                else: safe_print("❌ Invalid choice")
    safe_print("\n" + "=" * 80)
    safe_print(f"✅ Fixed {fixed_count} occurrence(s)")

def main_menu():
    """Interactive main menu."""
    while True:
        safe_print("\n" + "=" * 80)
        safe_print("🔧 UNDERSCORE VARIABLE FIXER - Main Menu")
        safe_print("=" * 80)
        safe_print("\nWhat would you like to do?")
        safe_print("\n  1. 🎯 Interactive fix: Review each lazy _ placeholder")
        safe_print("  2. ⚡ Auto-fix: Replace all lazy _ → 'unused' (preserves _('text'))")
        safe_print("  3. 🔍 Review 'unused': Improve names for 'unused' variables")
        safe_print("  4. 🌍 Auto-fix missing i18n imports: Inject 'from omnipkg.i18n import _'")
        safe_print("  5. 📋 Review ignore list: Check skipped items")
        safe_print("  6. 🚪 Exit")
        
        choice = input("\nYour choice (1-6): ").strip()
        
        if choice == '1':
            interactive_mode('_')
        elif choice == '2':
            count = auto_fix_all('_', 'unused')
            if count > 0: safe_print(f"\n💡 Tip: Run option 3 to review and improve these 'unused' names!")
        elif choice == '3':
            interactive_mode('unused')
        elif choice == '4':
            auto_fix_missing_imports()
        elif choice == '5':
            review_ignore_list()
        elif choice == '6':
            break
        else:
            safe_print("❌ Invalid choice. Please pick 1-6.")

if __name__ == '__main__':
    main_menu()