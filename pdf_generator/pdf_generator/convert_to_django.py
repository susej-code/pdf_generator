#!/usr/bin/env python3
"""
Convert Jinja2 template back to Django template syntax
Run this before integrating the template back into Django project
"""

import re
import sys

def jinja2_to_django(template_content):
    """Convert Jinja2 template syntax to Django template syntax"""
    
    # Convert default filter syntax
    # |default("value") -> |default:"value"
    template_content = re.sub(
        r'\|default\((["\'])(.*?)\1\)',
        r'|default:\1\2\1',
        template_content
    )
    
    # Convert loop variables
    # loop.first -> forloop.first
    template_content = template_content.replace('loop.first', 'forloop.first')
    # loop.index -> forloop.counter
    template_content = template_content.replace('loop.index', 'forloop.counter')
    # loop.index0 -> forloop.counter0 (if any)
    template_content = template_content.replace('loop.index0', 'forloop.counter0')
    
    return template_content

def main():
    input_file = 'template.html'
    output_file = 'template_django.html'
    
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    
    print(f"Converting {input_file} from Jinja2 to Django syntax...")
    
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    converted = jinja2_to_django(content)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(converted)
    
    print(f"✓ Converted template saved to: {output_file}")
    print("\nDifferences found and converted:")
    print("- |default('value') → |default:'value'")
    print("- loop.first → forloop.first")
    print("- loop.index → forloop.counter")
    print("\n⚠️  Note: Complex Jinja2 features may need manual review")

if __name__ == "__main__":
    main()