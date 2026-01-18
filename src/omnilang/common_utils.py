#!/usr/bin/env python3
"""
Common configuration utilities for omnilang.
This module has NO dependencies on other omnilang modules to prevent circular imports.
"""
import json
import os
from pathlib import Path


def get_project_root():
    """
    Get the configured project root from config file.
    Falls back to current directory only if config doesn't exist.
    
    Returns:
        Path: The project root directory
    """
    config_home = os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')
    config_path = Path(config_home) / 'omnilang' / 'i18n_config.json'
    
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                project_root = Path(config['project_root'])
                if project_root.exists():
                    return project_root
        except:
            pass
    
    # Fallback to cwd only if no config
    return Path.cwd()


def get_config_path():
    """Returns a user-writable path for the config file."""
    config_home = os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')
    config_dir = Path(config_home) / 'omnilang'
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / 'i18n_config.json'


def detect_source_directories(project_root=None):
    """
    Intelligently detect source directories for the project.
    Handles both src/ and non-src/ project structures.
    
    Returns:
        list[Path]: List of source directories to process
    """
    if project_root is None:
        project_root = get_project_root()
    
    source_dirs = []
    
    # Strategy 1: Check for src/PACKAGE structure (modern Python packaging)
    src_dir = project_root / 'src'
    if src_dir.exists():
        # Find all packages inside src/
        for item in src_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.') and not item.name.startswith('_'):
                # Check if it's a Python package (has __init__.py or .py files)
                if (item / '__init__.py').exists() or any(item.glob('*.py')):
                    print(f"  📦 Found package: {item.relative_to(project_root)}")
                    source_dirs.append(item)
    
    # Strategy 2: If no src/ structure, look for package in project root
    if not source_dirs:
        for item in project_root.iterdir():
            if item.is_dir() and not item.name.startswith('.') and item.name not in ['build', 'dist', 'tests', 'docs', 'venv', 'env']:
                if (item / '__init__.py').exists():
                    print(f"  📦 Found package: {item.name}")
                    source_dirs.append(item)
                    break
    
    # Strategy 3: Always add tests/ if it exists (check both src/tests and tests/)
    for tests_location in [project_root / 'src' / 'tests', project_root / 'tests']:
        if tests_location.exists() and tests_location not in source_dirs:
            print(f"  🧪 Found tests: {tests_location.relative_to(project_root)}")
            source_dirs.append(tests_location)
            break
    
    if not source_dirs:
        print(f"  ⚠️  No Python packages found in {project_root}")
    
    return source_dirs


def detect_locale_directory(project_root=None):
    """
    Find the locale directory in the project.
    PRIORITY: Look for existing locale dirs with actual translation files first!
    
    Returns:
        Path: The locale directory path
    """
    if project_root is None:
        project_root = get_project_root()
    
    # CRITICAL FIX: Check for directories that actually have translation files
    # Priority 1: Check src/PACKAGE/locale (most common for installed packages)
    src_dir = project_root / 'src'
    if src_dir.exists():
        for item in src_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                locale_candidate = item / 'locale'
                # Check if this locale dir has actual .po/.pot files
                if locale_candidate.exists():
                    if (list(locale_candidate.glob('**/*.po')) or 
                        list(locale_candidate.glob('*.pot'))):
                        print(f"  📍 Found active locale dir with translations: {locale_candidate.relative_to(project_root)}")
                        return locale_candidate
    
    # Priority 2: Check PACKAGE/locale (flat structure)
    for item in project_root.iterdir():
        if item.is_dir() and not item.name.startswith('.') and item.name not in ['build', 'dist', 'tests', 'docs', 'venv', 'env', 'src']:
            locale_candidate = item / 'locale'
            if locale_candidate.exists():
                if (list(locale_candidate.glob('**/*.po')) or 
                    list(locale_candidate.glob('*.pot'))):
                    print(f"  📍 Found active locale dir with translations: {locale_candidate.relative_to(project_root)}")
                    return locale_candidate
    
    # Priority 3: Check root-level locale directories (but only if they have files)
    for loc_name in ['locale', 'locales', 'translations']:
        locale_candidate = project_root / loc_name
        if locale_candidate.exists():
            if (list(locale_candidate.glob('**/*.po')) or 
                list(locale_candidate.glob('*.pot'))):
                print(f"  📍 Found active locale dir with translations: {locale_candidate.relative_to(project_root)}")
                return locale_candidate
    
    # If we get here, no locale dir with translations exists
    # Create one in the best location based on project structure
    if src_dir.exists():
        # Find the main package
        for item in src_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                if (item / '__init__.py').exists():
                    default_locale = item / 'locale'
                    print(f"  🆕 Will create locale dir: {default_locale.relative_to(project_root)}")
                    return default_locale
    
    # Fallback: locale/ in project root
    default_locale = project_root / 'locale'
    print(f"  🆕 Will create locale dir: locale/")
    return default_locale


def detect_pot_file(project_root=None, locale_dir=None):
    """
    Find or determine the .pot template file location.
    
    Returns:
        Path: The .pot file path
    """
    if project_root is None:
        project_root = get_project_root()
    
    if locale_dir is None:
        locale_dir = detect_locale_directory(project_root)
    
    # Look for existing .pot files in the locale directory
    pot_files = list(locale_dir.glob('*.pot'))
    if pot_files:
        return pot_files[0]
    
    # Determine package name from pyproject.toml or setup.py
    package_name = 'messages'  # default
    
    # Try pyproject.toml first
    pyproject = project_root / 'pyproject.toml'
    if pyproject.exists():
        try:
            import tomli
            with open(pyproject, 'rb') as f:
                data = tomli.load(f)
                package_name = data.get('project', {}).get('name', package_name)
        except:
            pass
    
    # Try setup.py if pyproject.toml didn't work
    if package_name == 'messages':
        setup_py = project_root / 'setup.py'
        if setup_py.exists():
            try:
                import re
                with open(setup_py, 'r') as f:
                    content = f.read()
                    # Look for name= in setup()
                    match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
                    if match:
                        package_name = match.group(1)
            except:
                pass
    
    return locale_dir / f'{package_name}.pot'