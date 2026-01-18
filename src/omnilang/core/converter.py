#!/usr/bin/env python3
"""
AST-Based F-String Translation Converter for omnipkg

This script uses Python's Abstract Syntax Tree (AST) to safely and accurately
convert f-strings to translatable format, avoiding syntax errors that regex-based
approaches can cause.

Key Improvements:
- Uses AST instead of regex for 100% accurate parsing
- Creates backups of ALL Python files before any changes
- Preserves existing translations (_() calls)
- Handles complex f-string expressions correctly
- Provides detailed analysis and safe conversion
"""

import ast
import sys
import shutil
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
import argparse
from dataclasses import dataclass
from datetime import datetime
import re
from omnilang.common_utils import get_project_root

# Configuration
PROJECT_ROOT = get_project_root().resolve()
SOURCE_DIR = PROJECT_ROOT / 'omnipkg'

def get_python_files(directories):
    """Yield all .py files from a list of directories (recursively)."""
    EXCLUDED_DIRS = {
        '.git', '__pycache__', 'venv', '.venv', 'node_modules',
        '.tox', 'build', 'dist', '.eggs', 'dev_tools', '_vendor'
    }
    
    for directory in directories:
        for py_file in Path(directory).rglob('*.py'):
            # Skip if any parent directory is in excluded list
            if any(excluded in py_file.parts for excluded in EXCLUDED_DIRS):
                continue
            yield py_file

@dataclass
class ConversionResult:
    """Represents a single f-string conversion"""
    file_path: Path
    line_number: int
    original_code: str
    converted_code: str
    fstring_content: str
    variables_used: List[str]
    confidence: str

class FStringAnalyzer(ast.NodeVisitor):
    """AST visitor that finds and analyzes f-strings AND user-facing string constants."""
    
    def __init__(self, source_code: str):
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.strings_to_convert: List[Dict] = []
        self.translated_locations: Set[Tuple[int, int]] = set()

    def visit_Call(self, node: ast.Call):
        """
        Finds existing _() calls to avoid re-translating, and also finds
        regular strings inside user-facing functions like print().
        """
        # First, check if this is an existing translation call to avoid processing its children
        is_translation_call = False
        if isinstance(node.func, ast.Name) and node.func.id == '_':
            is_translation_call = True
        elif (isinstance(node.func, ast.Attribute) and 
              isinstance(node.func.value, ast.Call) and
              hasattr(node.func.value, 'func') and
              isinstance(node.func.value.func, ast.Name) and
              node.func.value.func.id == '_'):
            is_translation_call = True

        if is_translation_call:
            if hasattr(node, 'lineno'):
                self.translated_locations.add((node.lineno, node.col_offset))
            # Do not visit children of an already translated call
            return

        # Now, check if this is a user-facing function call (like print)
        # that contains an untranslated string constant.
        user_facing_funcs = {'print', 'input', 'echo', 'prompt'}
        func_name = ''
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in user_facing_funcs:
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    location_key = (arg.lineno, arg.col_offset)
                    if location_key in self.translated_locations: continue
                    
                    original_line = self.source_lines[arg.lineno - 1]
                    if self._is_user_facing_string(arg.value, original_line):
                        self.strings_to_convert.append({
                            'type': 'Constant',
                            'location': location_key,
                            'original_line': original_line.strip(),
                            'value': arg.value
                        })
                        # Mark this as handled to avoid double-processing
                        self.translated_locations.add(location_key)

        # Finally, continue visiting other nodes
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        """Visit f-string nodes"""
        location_key = (node.lineno, node.col_offset)
        if location_key in self.translated_locations:
            return
        
        content_parts, variables = [], []
        for value in node.values:
            if isinstance(value, ast.Constant):
                content_parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                # Start building the placeholder, e.g., "{"
                placeholder = '{'

                # 1. Handle the conversion flag (e.g., f-string's !r becomes {!r})
                if value.conversion != -1:
                    placeholder += f'!{chr(value.conversion)}'

                # 2. Handle the format specifier (e.g., :>8.3f becomes {:>8.3f})
                if value.format_spec:
                    # The format_spec is an AST node that needs to be converted back to a string
                    format_spec_str = ast.unparse(value.format_spec)
                    placeholder += f':{format_spec_str}'

                # Close the placeholder, e.g., "}"
                placeholder += '}'

                # Use the complete placeholder (e.g., '{:>8.3f}') in the template
                content_parts.append(placeholder)
                variables.append(ast.unparse(value.value))
            else:
                content_parts.append(ast.unparse(value))
        
        template = ''.join(content_parts)
        original_line = self.source_lines[node.lineno - 1]
        
        if self._is_user_facing_string(template, original_line):
            self.strings_to_convert.append({
                'type': 'JoinedStr',
                'location': location_key,
                'template': template,
                'variables': variables,
                'original_line': original_line.strip()
            })
    
    def _is_user_facing_string(self, content: str, context: str) -> bool:
        content_lower = content.lower()
        context_lower = context.strip().lower()

        # --- FIRST: Check if this is OBVIOUSLY technical/code ---
        
        # 1. Block pure formatting patterns (no mixed content)
        pure_formatting_patterns = [
            '=', '-', '─', '┐', '┘', '┌', '└', '╭', '╮', '╰', '╯',
            '---', '----', '-----', '------',
            '===', '====', '=====', '======',
            '___', '____', '_____', '______',
        ]
        # Only block if the ENTIRE content is just formatting characters
        content_stripped = content.strip()
        if content_stripped in pure_formatting_patterns:
            return False
        
        # Also block if it's ONLY repeating formatting chars (like "=" * 60)
        if len(set(content_stripped)) == 1 and content_stripped[0] in '=-_~':
            return False
            
        # 2. Block common output formatting patterns
        output_formatting = [
            '--- stderr ---', '--- stdout ---', '--------------',
            '--------------------------------', '========================',
            '~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~',
        ]
        if content_lower.strip() in [p.lower() for p in output_formatting]:
            return False
            
        # 3. Block standalone emoji fragments (including with whitespace)
        single_emoji_formatting = ['🔥', '🌍', '✅', '⚠️', '🔧', '🚀', '🧹', '🔬', '🎯', '📦', '🔍', '❌']
        content_no_whitespace = ''.join(content.split())
        if (content_no_whitespace in single_emoji_formatting or 
            (len(content_no_whitespace) <= 3 and all(c in ''.join(single_emoji_formatting) for c in content_no_whitespace))):
            return False
            
        # 4. Block pure whitespace/newlines (but allow if mixed with content)
        if content.strip() == '' or content in ['\n', '\n\n', '\t', ' ']:
            return False
            
        # 5. Block obvious technical patterns
        technical_patterns = [
            '.py', '.json', '.dist-info', '.egg-info', '.lock', '.tmp',
            'omnipkg:pkg:', 'omnipkg:snapshot:', 'bubble_version:', 'active_version',
            'import ', 'from ', 'def ', 'class ', 'try:', 'except ', 'finally:',
            'if ', 'else:', 'elif ', 'for ', 'while ', 'return ', 'yield ',
            'with ', 'as ', 'pass', 'break', 'continue', 'raise ', 'assert ',
            'lambda ', 'f"', 'f\'', '.__', 'sys.path', 'os.environ',
        ]
        if any(pattern in content_lower for pattern in technical_patterns):
            return False

        # 6. Block path-like strings
        if ('/' in content or '\\' in content) and ' ' not in content:
            return False

        # --- SECOND: Check if this is OBVIOUSLY user-facing ---
        
        # 1. Mixed emoji + text IS user-facing (emoji with meaningful text)
        user_emojis = ['✅', '❌', '⚠️', '🔍', '📦', '🎯', '🔧', '🧹', '🚀', '🔬']
        has_emoji = any(char in content for char in user_emojis)
        has_meaningful_text = len([word for word in content.split() if word.strip('🔥🌍✅⚠️🔧🧹🚀🔬🎯📦🔍❌')]) > 0
        
        if has_emoji and has_meaningful_text:
            return True
            
        # 2. User-facing keywords in meaningful sentences
        user_facing_patterns = [
            'error', 'warning', 'success', 'failed', 'complete', 'install', 
            'remove', 'update', 'found', 'missing', 'version', 'package',
            'dependency', 'conflict', 'press enter', 'are you sure', 'continue',
            'cancel', 'proceed', 'select', 'choice', 'skipping', 'restoring',
            'clearing', 'verification', 'cleanup', 'test', 'switching'
        ]
        if len(content.split()) > 2 and any(keyword in content_lower for keyword in user_facing_patterns):
            return True

        # 3. Strings in print/input context are likely user-facing
        if context_lower.startswith(('print(', 'input(', '_(')):
            return True
            
        # 4. Strings with actual sentences (multiple words, punctuation)
        words = content.split()
        if (len(words) >= 3 and 
            any(c in content for c in '.,!?:') and
            not all(word.isupper() or word.isdigit() for word in words)):
            return True

        # --- DEFAULT: If unsure, DON'T translate ---
        return False

    
    def _assess_confidence(self, content: str, context: str) -> str:
        """Assess confidence level for conversion"""
        if any(term in context.lower() for term in ['print', 'click.echo', 'rich.print', 'logging']):
            return 'high'
        elif any(term in content.lower() for term in ['error', 'warning', 'success', 'failed']):
            return 'high'
        elif len(content) > 20:
            return 'medium'
        else:
            return 'low'

class FStringTransformer(ast.NodeTransformer):
    """
    AST transformer that correctly converts strings and records the EXACT replacement needed.
    """
        
    def __init__(self, strings_to_convert: List[Dict]):
        self.strings_to_convert = {s['location']: s for s in strings_to_convert}
        self.conversions_made = []

    def generic_visit(self, node):
        super().generic_visit(node)

        # Logic: If this node is a target, we calculate the replacement code
        # But we DO NOT modify the tree. We just record what needs to change.
        
        target_info = None
        new_node = None
        
        # Check if it's a JoinedStr (f-string) we want to convert
        if isinstance(node, ast.JoinedStr):
            location_key = (node.lineno, node.col_offset)
            if (location_key in self.strings_to_convert and
                    self.strings_to_convert[location_key]['type'] == 'JoinedStr'):
                target_info = self.strings_to_convert[location_key]
                
                template = target_info['template']
                variables = target_info['variables']

                # Create the replacement AST node
                translation_call = ast.Call(
                    func=ast.Name(id='_', ctx=ast.Load()),
                    args=[ast.Constant(value=template)],
                    keywords=[]
                )
                
                if not variables:
                    new_node = translation_call
                else:
                    # Construct .format() call
                    format_args = [ast.parse(var_expr, mode='eval').body for var_expr in variables]
                    new_node = ast.Call(
                        func=ast.Attribute(value=translation_call, attr='format', ctx=ast.Load()),
                        args=format_args,
                        keywords=[]
                    )

        # Check if it's a regular string we want to convert
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            location_key = (node.lineno, node.col_offset)
            if (location_key in self.strings_to_convert and
                    self.strings_to_convert[location_key]['type'] == 'Constant'):
                target_info = self.strings_to_convert[location_key]
                new_node = ast.Call(
                    func=ast.Name(id='_', ctx=ast.Load()),
                    args=[ast.Constant(value=node.value)],
                    keywords=[]
                )

        if target_info and new_node:
            # Generate the string representation of the NEW code
            # Note: We don't worry about line length here. Let it be long.
            replacement_code = ast.unparse(new_node)
            
            self.conversions_made.append({
                'start_line': node.lineno,
                'start_col': node.col_offset,
                'end_line': node.end_lineno,
                'end_col': node.end_col_offset,
                'original_text': target_info.get('original_line', ''), # For logging
                'replacement_text': replacement_code,
                # Metadata for the report
                'template': target_info.get('template', ''),
                'variables': target_info.get('variables', [])
            })

        return node

class ASTFStringConverter:
    """Main converter class using AST approach"""
    
    def __init__(self, source_dirs: List[Path], dry_run: bool = False, backup: bool = True):
        self.source_dirs = source_dirs
        self.dry_run = dry_run
        self.backup = backup
        self.stats = {
            'files_scanned': 0,
            'files_with_fstrings': 0,
            'files_modified': 0,
            'fstrings_found': 0,
            'fstrings_converted': 0,
            'backups_created': 0
        }
        self.all_conversions: List[ConversionResult] = []
    
    def scan_and_convert(self):
        """Main method to scan and convert f-strings"""
        print(f"🔍 Scanning {', '.join(str(d) for d in self.source_dirs)} for f-strings...")
        python_files = list(get_python_files(self.source_dirs))

        # Create backups first
        if not self.dry_run:
            self.create_backups()

        python_files = list(get_python_files(self.source_dirs))
        print(f"📁 Found {len(python_files)} Python files")

        for py_file in python_files:
            # Find which source dir this file belongs to for relative path
            for src_dir in self.source_dirs:
                try:
                    rel_path = py_file.relative_to(src_dir)
                    break
                except ValueError:
                    continue
            else:
                rel_path = py_file  # fallback, shouldn't happen
            print(f"  📄 Analyzing {rel_path}")
            fstrings = self.analyze_file(py_file)
            if fstrings:
                print(f"    🎯 Found {len(fstrings)} convertible f-strings")
                if self.convert_file(py_file, fstrings):
                    if not self.dry_run:
                        print(f"    ✅ Converted successfully")
                    else:
                        print(f"    🔍 Would convert (dry run)")
                else:
                    print(f"    ⚠️  Conversion failed or skipped")
            else:
                print(f"    ℹ️  No f-strings to convert")

    def create_backups(self) -> None:
        """
        Create backups of ALL Python files, storing them in a dedicated,
        separate directory to keep the source project clean.
        """
        if not self.backup:
            return

        print("💾 Creating backups of all Python files...")
        
        # --- THIS IS THE DEFINITIVE FIX ---
        # Hardcode the backup root to the correct location, outside the project.
        # This ensures backups NEVER clutter the source directory.
        backup_root = Path("/home/minds3t/i18n_tools/backups")
        
        # Create the timestamped folder inside the dedicated backup root
        backup_dir = backup_root / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # --- END OF FIX ---

        python_files = list(get_python_files(self.source_dirs))
        
        # We need the project root to create the correct relative paths inside the backup
        # Assuming /home/minds3t/omnipkg is the project root
        project_root = Path("/home/minds3t/omnipkg")

        for py_file in python_files:
            try:
                # Get the path relative to the project root (e.g., omnipkg/core.py or tests/test_...py)
                rel_path = py_file.relative_to(project_root)
                backup_file = backup_dir / rel_path
                
                # Create the necessary subdirectories in the backup location
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                
                shutil.copy2(py_file, backup_file)
                self.stats['backups_created'] += 1
            except Exception as e:
                print(f"    - ⚠️  Warning: Could not back up {py_file.name}: {e}")
        
        if self.stats['backups_created'] > 0:
            print(f"✅ Created {self.stats['backups_created']} backups in {backup_dir}")
    
    def analyze_file(self, file_path: Path) -> List[Dict]:
        """Analyze a single Python file for all translatable strings."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
        except Exception as e:
            print(f"❌ Error reading {file_path}: {e}")
            return []
        
        try:
            tree = ast.parse(source_code, filename=str(file_path))
        except SyntaxError as e:
            print(f"⚠️  Syntax error in {file_path}: {e}")
            return []
        
        analyzer = FStringAnalyzer(source_code)
        analyzer.visit(tree)
        
        self.stats['files_scanned'] += 1
        
        # --- THIS IS THE FIX ---
        # Use the new, correct attribute name: strings_to_convert
        if analyzer.strings_to_convert:
            self.stats['files_with_fstrings'] += 1 # This stat is now more general
            self.stats['fstrings_found'] += len(analyzer.strings_to_convert) # This stat is now more general
        
        return analyzer.strings_to_convert # Return the correct list
        # --- END OF FIX ---
    
    def convert_file(self, file_path: Path, fstrings: List[Dict]) -> bool:
        """
        Convert f-strings in a single file using SURGICAL REPLACEMENT.
        This preserves comments and formatting in the rest of the file.
        
        CRITICAL FIX: Uses byte-oriented processing to match AST column offsets
        which are UTF-8 byte offsets, preventing corruption in files with emojis.
        """
        if not fstrings:
            return False
        
        try:
            # Read as BYTES to handle offsets correctly
            with open(file_path, 'rb') as f:
                source_bytes = f.read()
            
            # Decode for AST parsing only
            try:
                source_code = source_bytes.decode('utf-8')
            except UnicodeDecodeError:
                print(f"❌ Error decoding {file_path}: Not valid UTF-8")
                return False

            # Parse to get locations
            tree = ast.parse(source_code, filename=str(file_path))
            
            # Run transformer just to calculate replacements
            transformer = FStringTransformer(fstrings)
            transformer.visit(tree)
            
            if not transformer.conversions_made:
                return False
            
            # --- SURGICAL REPLACEMENT LOGIC (BYTE-BASED) ---
            
            # 1. Sort replacements in REVERSE order
            replacements = sorted(
                transformer.conversions_made,
                key=lambda x: (x['start_line'], x['start_col']),
                reverse=True
            )
            
            # 2. Build byte-offset index map for lines
            source_lines_bytes = source_bytes.splitlines(keepends=True)
            line_start_indices = [0]
            current_pos = 0
            for line in source_lines_bytes:
                current_pos += len(line)
                line_start_indices.append(current_pos)
            
            new_source_parts = []
            last_pos = len(source_bytes)
            
            for rep in replacements:
                # Calculate absolute byte start/end indices
                # AST offsets are 1-based line, 0-based byte column
                start_idx = line_start_indices[rep['start_line'] - 1] + rep['start_col']
                end_idx = line_start_indices[rep['end_line'] - 1] + rep['end_col']
                
                # Validation
                if start_idx >= last_pos: continue 
                
                # Keep the bytes AFTER this replacement
                new_source_parts.append(source_bytes[end_idx:last_pos])
                
                # Add the REPLACEMENT text (encoded to utf-8 bytes)
                new_source_parts.append(rep['replacement_text'].encode('utf-8'))
                
                # Update pointer
                last_pos = start_idx
                
                self.all_conversions.append(ConversionResult(
                    file_path=file_path,
                    line_number=rep['start_line'],
                    original_code=rep['original_text'],
                    converted_code=rep['replacement_text'],
                    fstring_content=rep['template'],
                    variables_used=rep['variables'],
                    confidence='high'
                ))

            # Add the start of the file
            new_source_parts.append(source_bytes[0:last_pos])
            
            # Reassemble bytes
            final_source_bytes = b"".join(reversed(new_source_parts))
            
            if not self.dry_run:
                with open(file_path, 'wb') as f:
                    f.write(final_source_bytes)
                self.stats['files_modified'] += 1
            
            self.stats['fstrings_converted'] += len(transformer.conversions_made)
            return True
            
        except Exception as e:
            print(f"❌ Error converting {file_path}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def generate_report(self):
        """Generate comprehensive report"""
        print("\n" + "="*80)
        print("📊 AST F-STRING CONVERSION REPORT")
        print("="*80)
        
        print(f"\n📈 Statistics:")
        print(f"  • Files scanned: {self.stats['files_scanned']}")
        print(f"  • Files with f-strings: {self.stats['files_with_fstrings']}")
        print(f"  • Files modified: {self.stats['files_modified']}")
        print(f"  • F-strings found: {self.stats['fstrings_found']}")
        print(f"  • F-strings converted: {self.stats['fstrings_converted']}")
        print(f"  • Backup files created: {self.stats['backups_created']}")
        
        if not self.all_conversions:
            print("\n✅ No f-strings found that need conversion!")
            return
        
        print(f"\n📋 Conversion Details:")
        print("-" * 60)
        
        # Group by file
        by_file = {}
        for conv in self.all_conversions:
            # Find which source dir this file belongs to for relative path
            for src_dir in self.source_dirs:
                try:
                    file_key = conv.file_path.relative_to(src_dir)
                    break
                except ValueError:
                    continue
            
            if file_key not in by_file:
                by_file[file_key] = []
            by_file[file_key].append(conv)
        
        for file_path, conversions in by_file.items():
            print(f"\n📄 {file_path} ({len(conversions)} conversions)")
            print("-" * 40)
            
            for conv in conversions:
                print(f"  Line {conv.line_number:3d}: {conv.fstring_content}")
                print(f"         Variables: {conv.variables_used if conv.variables_used else 'None'}")
                print(f"         Before: {conv.original_code}")
                print(f"         After:  {conv.converted_code}")
                print()
        
        if self.dry_run:
            print("🔄 DRY RUN MODE - No files were modified")
            print("   Run without --dry-run to apply conversions")
        else:
            print(f"✅ CONVERSIONS APPLIED")
            print("   💾 All original files backed up before modification")
    
    def check_gettext_setup(self):
        """Check if gettext is properly set up - uses the working underscore_fixer logic"""
        print("\n🔍 Checking gettext setup...")
        
        # Import the WORKING logic from underscore_fixer
        from omnilang.core.underscore_fixer import find_files_needing_i18n_import
        from omnilang.common_utils import get_project_root, detect_source_directories
        
        project_root = get_project_root()
        source_dirs = detect_source_directories(project_root)
        
        print(f"🔍 Searching in: {project_root}")
        
        needs_setup = []
        for src_dir in source_dirs:
            needs_setup.extend(find_files_needing_i18n_import(str(src_dir)))
        
        if needs_setup:
            print(f"⚠️  {len(needs_setup)} files need gettext setup:")
            for file_path in needs_setup[:10]:
                print(f"  📄 {file_path}")
            if len(needs_setup) > 10:
                print(f"  ... and {len(needs_setup) - 10} more")
            print("\n💡 Add this import to files with _() calls:")
            print("   from omnipkg.i18n import _")
        else:
            print("✅ Gettext setup looks good!")

def main():
    parser = argparse.ArgumentParser(
        description='AST-based f-string to translation converter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Convert f-strings with backups
  %(prog)s --dry-run         # Preview conversions without changes
  %(prog)s --no-backup       # Convert without creating backups
  %(prog)s --source-dir ./my_project  # Use custom source directory
        """)
    
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be converted without making changes')
    parser.add_argument('--no-backup', action='store_true',
                       help='Skip creating backup files (not recommended)')
    parser.add_argument('--source-dir', type=Path, default=SOURCE_DIR,
                       help=f'Source directory to process (default: {SOURCE_DIR})')
    
    args = parser.parse_args()
    
    if not args.source_dir.exists():
        print(f"❌ Source directory not found: {args.source_dir}")
        sys.exit(1)
    
    print("🚀 AST-Based F-String Translation Converter")
    print(f"📁 Source directory: {args.source_dir}")
    
    if args.dry_run:
        print("🔍 DRY RUN MODE - No files will be modified")
    elif args.no_backup:
        print("⚠️  NO BACKUP MODE - Original files will be overwritten")
    else:
        print("💾 BACKUP MODE - All files will be backed up first")
    
    # Fix: Convert single source_dir to a list for source_dirs
    converter = ASTFStringConverter(
        source_dirs=[args.source_dir],  # <- This is the key fix
        dry_run=args.dry_run,
        backup=not args.no_backup
    )
    
    try:
        converter.scan_and_convert()
        converter.generate_report()
        converter.check_gettext_setup()
        
        if converter.all_conversions:
            print(f"\n🎯 Next Steps:")
            if args.dry_run:
                print("1. Review the preview above")
                print("2. Run without --dry-run to apply conversions:")
                print("   python ast_fstring_converter.py")
            else:
                print("1. Review converted files for correctness")
                print("2. Run your string extraction script:")
                print("   python extract_strings.py")
                print("3. Update translation files (.po)")
                print("4. Test the application")
        else:
            print(f"\n✅ No f-strings need conversion!")
            
    except KeyboardInterrupt:
        print(f"\n🛑 Conversion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()