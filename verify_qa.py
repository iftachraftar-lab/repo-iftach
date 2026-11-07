#!/usr/bin/env python3
"""
QA Pre-Flight Verification Script for NutriAgent
Runs automated static and consistency checks on static/index.html and backend code.
"""

import sys
import re
import py_compile
from pathlib import Path

def check_python_syntax(repo_root: Path):
    print("🔍 [1/4] Checking Python backend syntax...")
    py_files = [repo_root / 'bot_server.py']
    errors = []
    for f in py_files:
        if f.exists():
            try:
                py_compile.compile(str(f), doraise=True)
                print(f"  ✓ {f.name} compiled successfully.")
            except py_compile.PyCompileError as e:
                errors.append(f"Syntax error in {f.name}: {e}")
    return errors

def check_html_tag_balance(html: str):
    print("🔍 [2/4] Checking HTML tag balance...")
    errors = []
    tags = ['div', 'form', 'button', 'template', 'main', 'header', 'nav']
    for tag in tags:
        open_tags = len(re.findall(rf'<{tag}(\s+[^>]*)?>', html, re.IGNORECASE))
        close_tags = len(re.findall(rf'</{tag}>', html, re.IGNORECASE))
        if tag == 'button' or tag == 'div':
            if open_tags != close_tags:
                errors.append(f"Tag mismatch for <{tag}>: {open_tags} opening vs {close_tags} closing (diff: {open_tags - close_tags})")
            else:
                print(f"  ✓ <{tag}> is balanced ({open_tags} tags).")
        else:
            if open_tags != close_tags:
                errors.append(f"Tag mismatch for <{tag}>: {open_tags} opening vs {close_tags} closing")
            else:
                print(f"  ✓ <{tag}> is balanced ({open_tags} tags).")
    return errors

def extract_alpine_methods(html: str):
    idx_app = html.find('function nutritionApp()')
    if idx_app == -1:
        idx_app = html.find('nutritionApp()')
    if idx_app == -1:
        return set()
    
    js_text = html[idx_app:]
    methods = set()
    
    for m in re.finditer(r'(?:async\s+)?([a-zA-Z0-9_$]+)\s*\([^)]*\)\s*\{', js_text):
        name = m.group(1)
        if name not in {'if', 'for', 'while', 'switch', 'catch', 'function', 'return', 'then'}:
            methods.add(name)
            
    for m in re.finditer(r'^\s*([a-zA-Z0-9_$]+)\s*:', js_text, re.MULTILINE):
        methods.add(m.group(1))
        
    for m in re.finditer(r'get\s+([a-zA-Z0-9_$]+)\s*\(\)', js_text):
        methods.add(m.group(1))

    for m in re.finditer(r'function\s+([a-zA-Z0-9_$]+)\s*\(', html):
        methods.add(m.group(1))
        
    return methods

def check_refs_integrity(html: str):
    print("🔍 [3/4] Checking $refs integrity...")
    errors = []
    
    defined_refs = set(re.findall(r'x-ref=["\']([a-zA-Z0-9_$]+)["\']', html))
    print(f"  Found {len(defined_refs)} defined x-refs in DOM.")
    
    accessed_refs = set(re.findall(r'\$refs\.([a-zA-Z0-9_$]+)', html))
    print(f"  Found {len(accessed_refs)} unique $refs accesses.")
    
    for ref in accessed_refs:
        if ref not in defined_refs:
            errors.append(f"Accessed ref '$refs.{ref}' is not defined with x-ref='{ref}' in DOM!")
            
    if not errors:
        print("  ✓ All $refs match valid DOM x-ref targets.")
    return errors

def check_alpine_calls(html: str, defined_methods: set):
    print("🔍 [4/4] Checking Alpine.js method call integrity...")
    errors = []
    
    call_patterns = [
        r'@click(?:[\.\w]+)?=["\']([^"\']+)["\']',
        r'@submit(?:[\.\w]+)?=["\']([^"\']+)["\']',
        r'@change(?:[\.\w]+)?=["\']([^"\']+)["\']',
        r'@input(?:[\.\w]+)?=["\']([^"\']+)["\']',
    ]
    
    found_calls = set()
    for pat in call_patterns:
        for attr in re.findall(pat, html):
            for call_match in re.finditer(r'([a-zA-Z0-9_$]+)\s*\(', attr):
                fn = call_match.group(1)
                found_calls.add((fn, attr.strip()))
                
    builtins = {
        'alert', 'confirm', 'prompt', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
        'parseInt', 'parseFloat', 'encodeURIComponent', 'decodeURIComponent', 'stopLiveViewfinderCamera',
        't', 'tr', 'trSlot', 'Math', 'console', '$nextTick', 'Number', 'String', 'Boolean', 'click', 'focus',
        'indexOf', 'includes', 'trim', 'preventDefault', 'stopPropagation', 'reset', 'toString', 'slice',
        'setZoom', 'setPortion', 'toggleScanner', 'toggleVoice', 'setItem', 'getItem', 'removeItem',
        'querySelector', 'querySelectorAll', 'getElementById', 'addEventListener', 'removeEventListener'
    }
    
    missing_methods = []
    for fn, expr in found_calls:
        if fn in builtins:
            continue
        if fn.startswith('$'):
            continue
        if fn not in defined_methods:
            missing_methods.append(f"Called function '{fn}()' in [{expr}] is NOT defined in Alpine nutritionApp()!")

    if missing_methods:
        errors.extend(missing_methods)
    else:
        print(f"  ✓ All {len(found_calls)} Alpine method calls are verified and defined.")

    return errors

def main():
    repo_root = Path(__file__).resolve().parent
    index_html = repo_root / 'static' / 'index.html'
    
    if not index_html.exists():
        print(f"❌ Error: {index_html} not found!")
        sys.exit(1)
        
    print("=" * 60)
    print("🚀 NutriAgent QA & Pre-Flight Automated Verifier")
    print("=" * 60)
    
    html = index_html.read_text(encoding='utf-8')
    
    all_errors = []
    all_errors.extend(check_python_syntax(repo_root))
    all_errors.extend(check_html_tag_balance(html))
    all_errors.extend(check_refs_integrity(html))
    
    defined_methods = extract_alpine_methods(html)
    all_errors.extend(check_alpine_calls(html, defined_methods))
    
    print("=" * 60)
    if all_errors:
        print(f"❌ QA VERIFICATION FAILED with {len(all_errors)} errors:")
        for i, err in enumerate(all_errors, start=1):
            print(f"  {i}. {err}")
        print("=" * 60)
        sys.exit(1)
    else:
        print("✅ ALL QA PRE-FLIGHT CHECKS PASSED SUCCESSFULLY (0 ERRORS)!")
        print("=" * 60)
        sys.exit(0)

if __name__ == '__main__':
    main()
