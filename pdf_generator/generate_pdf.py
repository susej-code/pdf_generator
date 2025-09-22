#!/usr/bin/env python3
"""
Standalone PDF Generator for B2C Policy Reports
This script can be run independently without Django to test PDF generation and styling
"""

import os
import json
import glob
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
    
    # Create output directory and clean existing files
    output_dir = os.path.join(current_dir, 'output')
    os.makedirs(output_dir, exist_ok=True)
    
    # Clean existing output files
    existing_files = glob.glob(os.path.join(output_dir, '*'))
    if existing_files:
        print(f"→ Cleaning {len(existing_files)} existing file(s) from output directory...")
        for file_path in existing_files:
            try:
                os.remove(file_path)
                print(f"  ✓ Removed: {os.path.basename(file_path)}")
            except OSError as e:
                print(f"  ✗ Failed to remove {os.path.basename(file_path)}: {e}")
    
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
    
    # Format dates properly
    # Convert policy date from ISO format to desired format
    if 'policy_report' in context and 'date' in context['policy_report']:
        date_str = context['policy_report']['date']
        try:
            # Parse ISO date and convert to desired format
            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            context['policy_report']['formatted_date'] = date_obj.strftime("%b %d, %Y, %H:%M %p")
        except ValueError:
            context['policy_report']['formatted_date'] = "Sep 18, 2025, 19:29 PM"
    
    # Add current date in same format
    context['generated_date'] = datetime.now().strftime("%b %d, %Y, %H:%M %p")
    
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