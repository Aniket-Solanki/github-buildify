"""
Utility to export resume data to PDF using ReportLab.
"""

import io
from typing import Dict, Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


def generate_resume_pdf(resume_data: Dict[str, Any]) -> io.BytesIO:
    """
    Generate a PDF resume from structured resume data.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=50, leftMargin=50,
                            topMargin=50, bottomMargin=50)

    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=16,
        textColor=colors.HexColor("#1e293b")
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        spaceAfter=10,
        spaceBefore=15,
        textColor=colors.HexColor("#334155")
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=11,
        spaceAfter=6,
        leading=16,
        textColor=colors.HexColor("#475569")
    )
    
    bullet_style = ParagraphStyle(
        'CustomBullet',
        parent=normal_style,
        leftIndent=15,
        firstLineIndent=-10,
    )

    # New style for code metrics
    metrics_style = ParagraphStyle(
        'MetricsStyle',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=8,
        leading=14,
        textColor=colors.HexColor("#64748b"),
    )

    story = []

    # Project Title
    if resume_data.get('project_title'):
        story.append(Paragraph(resume_data['project_title'], title_style))

    # Tech Stack
    if resume_data.get('tech_stack'):
        story.append(Paragraph("<b>Technology Stack</b>", heading_style))
        story.append(Paragraph(resume_data['tech_stack'], normal_style))
        story.append(Spacer(1, 10))

    # Code Metrics (new)
    if resume_data.get('code_metrics'):
        story.append(Paragraph("<b>Code Metrics</b>", heading_style))
        metrics = resume_data['code_metrics']
        metrics_text = f"\nTotal Functions: {metrics.get('total_functions', 0)}\n"
        metrics_text += f"Total Classes: {metrics.get('total_classes', 0)}\n"
        languages = metrics.get('languages', {})
        if languages:
            lang_list = ', '.join(languages.keys())
            metrics_text += f"Languages: {lang_list}\n"
        story.append(Paragraph(metrics_text, metrics_style))
        story.append(Spacer(1, 10))

    # Key Contributions (Bullet Points)
    if resume_data.get('bullet_points'):
        story.append(Paragraph("<b>Key Contributions & Details</b>", heading_style))
        for bullet in resume_data['bullet_points']:
            # Strip markdown bolding for pdf simplicity, or replace with html <b>
            bullet_clean = bullet.replace("**", "<b>", 1).replace("**", "</b>", 1)
            # handle remaining ** just in case
            bullet_clean = bullet_clean.replace("**", "") 
            story.append(Paragraph(f"• {bullet_clean}", bullet_style))
        story.append(Spacer(1, 10))

    # Additional Notes
    if resume_data.get('additional_notes'):
        story.append(Paragraph("<b>Additional Details</b>", heading_style))
        story.append(Paragraph(resume_data['additional_notes'].replace('\n', '<br/>'), normal_style))
        story.append(Spacer(1, 10))

    # Future Plans
    if resume_data.get('future_plans'):
        story.append(Paragraph("<b>Future Plans</b>", heading_style))
        story.append(Paragraph(resume_data['future_plans'].replace('\n', '<br/>'), normal_style))

    doc.build(story)
    buffer.seek(0)
    return buffer
