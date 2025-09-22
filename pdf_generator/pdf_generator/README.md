# B2C Policy Report PDF Generator

This is a standalone PDF generator for styling B2C Policy Reports without needing the full Django environment.

## Setup

1. **Install Python 3.8+** (if not already installed)

2. **Create virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Generate a PDF:
```bash
python generate_pdf.py
```

This will:
- Load the template from `template.html`
- Use mock data from `mock_data.json`
- Generate `output/policy_report.pdf`
- Also save the rendered HTML as `output/policy_report.html` for debugging

## Files

### `template.html`
The HTML template using Jinja2 syntax. This is what you'll modify to style the PDF.

**Template Syntax Notes:**
- Template has been converted from Django to Jinja2:
  - `{% if loop.first %}` instead of `{% if forloop.first %}`
  - `{{ loop.index }}` instead of `{{ forloop.counter }}`
  - `|default("value")` instead of `|default:"value"`
  - Direct dictionary access (no Django model relationships)

**Important Notes:**
- xhtml2pdf does NOT support:
  - SVG files (convert to PNG)
  - Modern CSS features (flexbox partially works, grid doesn't)
  - External images (use base64 data URIs)
  - CSS variables (use actual color values)
  
### `mock_data.json`
Sample data from a real policy report. Structure:
```json
{
  "policy_report": {
    "policy_number": "POL-2024-001",
    "first_name": "John",
    "last_name": "Doe",
    ...
  },
  "questions_answers": [
    {
      "question": "What is the policy effective date?",
      "answer": "The policy effective date is May 28, 2022.",
      "policy_details": "The policy is effective from May 28, 2022...",
      "text_reference": "Policy Period: May 28, 2022 to May 28, 2023",
      "policy_reference_points": ["0", "1"]
    }
  ]
}
```

## Styling Tips

### Working with xhtml2pdf Limitations

1. **Images**: Convert SVGs to PNG and embed as base64:
   ```html
   <img src="data:image/png;base64,iVBORw0KG..." />
   ```

2. **Colors**: Replace CSS variables with actual values:
   ```css
   /* Don't use: */
   color: var(--color-orange-500);
   
   /* Use: */
   color: #F97316;
   ```

3. **Layout**: Use tables for complex layouts:
   ```html
   <table style="width: 100%;">
     <tr>
       <td style="width: 50%;">Left content</td>
       <td style="width: 50%;">Right content</td>
     </tr>
   </table>
   ```

4. **Page Breaks**: Force new pages:
   ```css
   .page-break {
     page-break-after: always;
   }
   ```

5. **Unicode Icons**: Use these instead of SVGs:
   - Arrow: ► (►)
   - Info: ● (●)
   - Check: ✓ (✓)
   - Star: ★ (★)

## Testing Changes

1. Modify `template.html`
2. Run `python generate_pdf.py`
3. Check `output/policy_report.pdf`
4. Debug using `output/policy_report.html` if needed

## Troubleshooting

**PDF looks different from HTML:**
- xhtml2pdf has limited CSS support
- Check the console output for warnings
- Use the HTML output to debug

**Images not showing:**
- Convert to base64 data URIs
- Or use Unicode symbols

**Layout broken:**
- Use tables instead of flexbox/grid
- Use inline styles for critical layout

## When Done

Send back the modified `template.html` file. The backend team will:
1. Convert Jinja2 back to Django template syntax using `convert_to_django.py`:
   ```bash
   python convert_to_django.py template.html template_django.html
   ```
2. Integrate into the main application
3. Test with real data

### Why Different Template Syntax?

**Django Project** uses Django's template engine:
- `{% if forloop.first %}`
- `|default:"value"`
- Rendered with Django's `render()` function

**This Standalone Generator** uses Jinja2:
- `{% if loop.first %}`
- `|default("value")`
- Simpler setup for FE developers

The converter script handles this automatically.