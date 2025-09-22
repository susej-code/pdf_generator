#!/usr/bin/env python3
"""
Standalone PDF Generator for B2C Policy Reports
This script can be run independently without Django to test PDF generation and styling
"""

import os
import json
from datetime import datetime
from jinja2 import Template
from xhtml2pdf import pisa
import io

def load_template(template_path):
    """Load HTML template from file"""
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()

def load_mock_data(data_path):
    """Load mock data from JSON file"""
    with open(data_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def generate_pdf(template_string, context, output_path):
    """Generate PDF from HTML template and context"""
    # Render template with context
    template = Template(template_string)
    html_string = template.render(**context)
    
    # Save rendered HTML for debugging
    html_output_path = output_path.replace('.pdf', '.html')
    with open(html_output_path, 'w', encoding='utf-8') as f:
        f.write(html_string)
    print(f"✓ HTML saved to: {html_output_path}")
    
    # Generate PDF
    with open(output_path, 'wb') as pdf_file:
        pisa_status = pisa.CreatePDF(
            html_string,
            dest=pdf_file,
            encoding='utf-8'
        )
    
    if pisa_status.err:
        print(f"✗ Error generating PDF: {pisa_status.err}")
        return False
    
    print(f"✓ PDF saved to: {output_path}")
    return True

def main():
    """Main function to generate PDF"""
    # Paths
    current_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(current_dir, 'template.html')
    data_path = os.path.join(current_dir, 'mock_data.json')
    output_path = os.path.join(current_dir, 'output', 'policy_report.pdf')
    
    # Create output directory
    os.makedirs(os.path.join(current_dir, 'output'), exist_ok=True)
    
    print("=" * 60)
    print("B2C Policy Report PDF Generator")
    print("=" * 60)
    
    # Check if files exist
    if not os.path.exists(template_path):
        print(f"✗ Template not found: {template_path}")
        return
    
    if not os.path.exists(data_path):
        print(f"✗ Mock data not found: {data_path}")
        return
    
    # Load template and data
    print(f"→ Loading template: {template_path}")
    template_string = load_template(template_path)
    
    print(f"→ Loading mock data: {data_path}")
    context = load_mock_data(data_path)
    
    # Add current date
    context['generated_date'] = datetime.now()
    
    # Generate PDF
    print(f"→ Generating PDF...")
    success = generate_pdf(template_string, context, output_path)
    
    if success:
        print("\n✅ PDF generated successfully!")
        print(f"   Open: {output_path}")
    else:
        print("\n❌ PDF generation failed!")
    
    print("=" * 60)

if __name__ == "__main__":
    main()