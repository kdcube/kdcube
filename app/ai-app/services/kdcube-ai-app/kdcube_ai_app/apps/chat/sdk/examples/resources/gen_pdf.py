"""
generate_test_files.py - Create test PDFs and images for multimodal testing
"""

import io
from pathlib import Path

def generate_test_pdfs():
    """Generate test PDF files using reportlab."""
    try:
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY
    except ImportError:
        print("❌ reportlab not installed. Installing...")
        import subprocess
        subprocess.check_call(["pip", "install", "reportlab"])
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#2C3E50'),
        spaceAfter=30,
        alignment=TA_CENTER
    )

    body_justified = ParagraphStyle(
        'BodyJustified',
        parent=styles['BodyText'],
        alignment=TA_JUSTIFY,
        spaceAfter=12
    )

    # ========== 1. MULTI-PAGE DOCUMENT (3 pages) ==========
    print("📄 Generating test_document.pdf (3 pages)...")
    doc = SimpleDocTemplate("test_document.pdf", pagesize=letter)
    story = []

    # PAGE 1: Executive Summary
    story.append(Paragraph("Annual Business Report 2025", title_style))
    story.append(Spacer(1, 0.3*inch))

    story.append(Paragraph("<b>Executive Summary</b>", styles['Heading2']))
    story.append(Paragraph(
        "This comprehensive annual report presents a detailed analysis of our company's "
        "performance throughout 2025. The year has been marked by significant growth, "
        "strategic expansions into new markets, and successful product launches that have "
        "positioned us as a leader in our industry.",
        body_justified
    ))

    story.append(Paragraph(
        "Our revenue increased by 45% year-over-year, reaching $12.8 million, while "
        "maintaining healthy profit margins of 28%. This growth was driven by three key factors: "
        "expansion into the European market, successful launch of our Enterprise tier product, "
        "and strategic partnerships with major industry players.",
        body_justified
    ))

    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("<b>Key Highlights</b>", styles['Heading2']))

    highlights_data = [
        ['Metric', '2025', '2024', 'Growth'],
        ['Total Revenue', '$12.8M', '$8.8M', '+45%'],
        ['Active Customers', '2,450', '1,620', '+51%'],
        ['Employee Count', '85', '62', '+37%'],
        ['Market Share', '18%', '12%', '+6pp'],
        ['Customer Satisfaction', '4.7/5', '4.5/5', '+0.2'],
    ]

    highlights_table = Table(highlights_data, colWidths=[2.2*inch, 1.5*inch, 1.5*inch, 1.2*inch])
    highlights_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
    ]))
    story.append(highlights_table)

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("<b>Strategic Achievements</b>", styles['Heading2']))
    story.append(Paragraph("• Successfully entered 5 new European markets (Germany, France, UK, Netherlands, Spain)", styles['BodyText']))
    story.append(Paragraph("• Launched Enterprise tier with advanced analytics and dedicated support", styles['BodyText']))
    story.append(Paragraph("• Formed strategic partnership with TechCorp International", styles['BodyText']))
    story.append(Paragraph("• Achieved SOC 2 Type II and ISO 27001 certifications", styles['BodyText']))
    story.append(Paragraph("• Expanded engineering team by 40% to support product roadmap", styles['BodyText']))

    # PAGE BREAK
    story.append(PageBreak())

    # PAGE 2: Financial Performance
    story.append(Paragraph("Financial Performance", title_style))
    story.append(Spacer(1, 0.3*inch))

    story.append(Paragraph("<b>Revenue Breakdown by Quarter</b>", styles['Heading2']))
    story.append(Paragraph(
        "Our quarterly performance showed consistent growth throughout the year, with Q4 "
        "being our strongest quarter at $3.8M in revenue. This represents a 62% increase "
        "compared to Q4 2024 and demonstrates the compounding effect of our growth strategies.",
        body_justified
    ))

    story.append(Spacer(1, 0.1*inch))

    quarterly_data = [
        ['Quarter', 'Revenue', 'New Customers', 'Churn Rate', 'Avg Deal Size'],
        ['Q1 2025', '$2.4M', '156', '3.2%', '$5,340'],
        ['Q2 2025', '$3.1M', '198', '2.8%', '$6,020'],
        ['Q3 2025', '$3.5M', '224', '2.5%', '$6,450'],
        ['Q4 2025', '$3.8M', '267', '2.1%', '$6,890'],
    ]

    quarterly_table = Table(quarterly_data, colWidths=[1.3*inch, 1.3*inch, 1.5*inch, 1.3*inch, 1.4*inch])
    quarterly_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2ecc71')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(quarterly_table)

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("<b>Revenue by Region</b>", styles['Heading2']))

    region_data = [
        ['Region', '2025 Revenue', '% of Total', '2024 Revenue', 'Growth'],
        ['North America', '$7.2M', '56%', '$6.8M', '+6%'],
        ['Europe', '$4.1M', '32%', '$1.5M', '+173%'],
        ['Asia Pacific', '$1.2M', '9%', '$0.4M', '+200%'],
        ['Other', '$0.3M', '3%', '$0.1M', '+200%'],
    ]

    region_table = Table(region_data, colWidths=[1.5*inch, 1.4*inch, 1.2*inch, 1.4*inch, 1.1*inch])
    region_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(region_table)

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("<b>Operating Expenses</b>", styles['Heading2']))
    story.append(Paragraph(
        "We maintained disciplined cost management while investing in growth. Total operating "
        "expenses for 2025 were $9.2M, resulting in a healthy EBITDA margin of 28%. "
        "Key expense categories include:",
        body_justified
    ))

    story.append(Paragraph("• Personnel costs: $5.1M (55% of expenses)", styles['BodyText']))
    story.append(Paragraph("• Sales & Marketing: $2.3M (25% of expenses)", styles['BodyText']))
    story.append(Paragraph("• Infrastructure & Technology: $1.2M (13% of expenses)", styles['BodyText']))
    story.append(Paragraph("• General & Administrative: $0.6M (7% of expenses)", styles['BodyText']))

    # PAGE BREAK
    story.append(PageBreak())

    # PAGE 3: Future Outlook & Action Items
    story.append(Paragraph("2026 Outlook & Strategic Priorities", title_style))
    story.append(Spacer(1, 0.3*inch))

    story.append(Paragraph("<b>Market Opportunities</b>", styles['Heading2']))
    story.append(Paragraph(
        "Looking ahead to 2026, we have identified several significant market opportunities "
        "that will drive our growth strategy. The total addressable market for our solutions "
        "is estimated at $2.4 billion and growing at 23% annually. We are well-positioned "
        "to capture an increasing share of this market.",
        body_justified
    ))

    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("<b>Strategic Priorities for 2026</b>", styles['Heading2']))

    story.append(Paragraph("<b>1. Product Innovation</b>", styles['Heading3']))
    story.append(Paragraph("• Launch AI-powered analytics module in Q2 2026", styles['BodyText']))
    story.append(Paragraph("• Release mobile applications for iOS and Android", styles['BodyText']))
    story.append(Paragraph("• Develop API marketplace for third-party integrations", styles['BodyText']))
    story.append(Spacer(1, 0.1*inch))

    story.append(Paragraph("<b>2. Market Expansion</b>", styles['Heading3']))
    story.append(Paragraph("• Enter Latin American market with focus on Brazil and Mexico", styles['BodyText']))
    story.append(Paragraph("• Establish physical presence in London and Berlin offices", styles['BodyText']))
    story.append(Paragraph("• Develop localized versions for key markets (German, French, Spanish)", styles['BodyText']))
    story.append(Spacer(1, 0.1*inch))

    story.append(Paragraph("<b>3. Team Growth</b>", styles['Heading3']))
    story.append(Paragraph("• Hire 35 new team members across engineering, sales, and support", styles['BodyText']))
    story.append(Paragraph("• Establish dedicated customer success team", styles['BodyText']))
    story.append(Paragraph("• Create leadership development program", styles['BodyText']))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("<b>2026 Financial Targets</b>", styles['Heading2']))

    targets_data = [
        ['Metric', '2026 Target', '2025 Actual', 'Required Growth'],
        ['Revenue', '$18.5M', '$12.8M', '+45%'],
        ['Customers', '3,500', '2,450', '+43%'],
        ['EBITDA Margin', '30%', '28%', '+2pp'],
        ['Employee NPS', '75', '68', '+7 points'],
    ]

    targets_table = Table(targets_data, colWidths=[2*inch, 1.5*inch, 1.5*inch, 1.7*inch])
    targets_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#9b59b6')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(targets_table)

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("<b>Key Action Items (Q1 2026)</b>", styles['Heading2']))
    story.append(Paragraph("1. Complete Series A funding round ($15M target) by end of Q1", styles['BodyText']))
    story.append(Paragraph("2. Launch enterprise sales campaign in DACH region", styles['BodyText']))
    story.append(Paragraph("3. Begin development of AI analytics module", styles['BodyText']))
    story.append(Paragraph("4. Hire VP of Engineering and VP of Sales", styles['BodyText']))
    story.append(Paragraph("5. Achieve 99.9% uptime SLA for Enterprise customers", styles['BodyText']))

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("<b>Conclusion</b>", styles['Heading2']))
    story.append(Paragraph(
        "2025 was a transformational year for our company. We've built a strong foundation "
        "for sustainable growth, assembled a talented team, and proven our product-market fit. "
        "As we look to 2026, we are confident in our ability to execute on our strategic "
        "priorities and continue our trajectory of rapid, profitable growth.",
        body_justified
    ))

    doc.build(story)
    print("✅ Created test_document.pdf (3 pages)")

    # ========== 2. Simple Invoice (1 page) ==========
    print("\n📄 Generating invoice.pdf...")
    doc = SimpleDocTemplate("invoice.pdf", pagesize=letter)
    story = []

    story.append(Paragraph("INVOICE", title_style))
    story.append(Spacer(1, 0.3*inch))

    invoice_data = [
        ['Invoice #:', 'INV-2025-001', 'Date:', '2025-01-15'],
        ['Customer:', 'Acme Corporation', 'Due Date:', '2025-02-15'],
    ]
    invoice_table = Table(invoice_data, colWidths=[1.5*inch, 2.5*inch, 1.5*inch, 2*inch])
    invoice_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.append(invoice_table)
    story.append(Spacer(1, 0.3*inch))

    items_data = [
        ['Item', 'Description', 'Qty', 'Price', 'Total'],
        ['Software License', 'Annual Enterprise License', '10', '$500', '$5,000'],
        ['Support Plan', 'Premium Support Package', '1', '$2,000', '$2,000'],
        ['Training', 'On-site Training (2 days)', '1', '$3,500', '$3,500'],
    ]

    items_table = Table(items_data, colWidths=[1.5*inch, 2.5*inch, 0.8*inch, 1*inch, 1*inch])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 0.3*inch))

    total_data = [
        ['Subtotal:', '$10,500'],
        ['Tax (10%):', '$1,050'],
        ['Total:', '$11,550'],
    ]
    total_table = Table(total_data, colWidths=[5.5*inch, 1.2*inch])
    total_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, -1), (-1, -1), 12),
        ('LINEABOVE', (0, -1), (-1, -1), 2, colors.black),
        ('TOPPADDING', (0, -1), (-1, -1), 12),
    ]))
    story.append(total_table)

    doc.build(story)
    print("✅ Created invoice.pdf (1 page)")

    # ========== 3. Q1 Report ==========
    print("\n📄 Generating report_q1.pdf...")
    doc = SimpleDocTemplate("report_q1.pdf", pagesize=letter)
    story = []

    story.append(Paragraph("Q1 2025 Financial Report", title_style))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("<b>Quarter Overview</b>", styles['Heading2']))
    story.append(Paragraph("Q1 2025 showed steady growth across all key metrics.", body_justified))
    story.append(Spacer(1, 0.1*inch))

    q1_data = [
        ['Metric', 'Q1 2025', 'Q4 2024', 'Change'],
        ['Revenue', '$2.4M', '$2.1M', '+14%'],
        ['Customers', '450', '420', '+7%'],
        ['Avg Deal Size', '$5.3K', '$5.0K', '+6%'],
    ]
    q1_table = Table(q1_data, colWidths=[2*inch, 1.5*inch, 1.5*inch, 1.2*inch])
    q1_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(q1_table)

    doc.build(story)
    print("✅ Created report_q1.pdf (1 page)")

    # ========== 4. Q2 Report ==========
    print("\n📄 Generating report_q2.pdf...")
    doc = SimpleDocTemplate("report_q2.pdf", pagesize=letter)
    story = []

    story.append(Paragraph("Q2 2025 Financial Report", title_style))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("<b>Quarter Overview</b>", styles['Heading2']))
    story.append(Paragraph("Q2 2025 demonstrated accelerated growth with strong market expansion.", body_justified))
    story.append(Spacer(1, 0.1*inch))

    q2_data = [
        ['Metric', 'Q2 2025', 'Q1 2025', 'Change'],
        ['Revenue', '$3.1M', '$2.4M', '+29%'],
        ['Customers', '520', '450', '+16%'],
        ['Avg Deal Size', '$6.0K', '$5.3K', '+13%'],
    ]
    q2_table = Table(q2_data, colWidths=[2*inch, 1.5*inch, 1.5*inch, 1.2*inch])
    q2_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2ecc71')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(q2_table)

    doc.build(story)
    print("✅ Created report_q2.pdf (1 page)")

    # ========== 5. Knowledge Base (2 pages) ==========
    print("\n📄 Generating knowledge_base.pdf (2 pages)...")
    doc = SimpleDocTemplate("knowledge_base.pdf", pagesize=letter)
    story = []

    story.append(Paragraph("Company Policy Handbook", title_style))
    story.append(Spacer(1, 0.3*inch))

    story.append(Paragraph("<b>Refund Policy</b>", styles['Heading2']))
    story.append(Paragraph(
        "We offer a 30-day money-back guarantee on all software purchases. "
        "To request a refund, contact support@company.com with your order number. "
        "Refunds are processed within 5-7 business days. The refund will be issued to "
        "the original payment method used for the purchase.",
        body_justified
    ))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("<b>Shipping Options</b>", styles['Heading2']))
    story.append(Paragraph(
        "We offer the following shipping methods:",
        body_justified
    ))
    story.append(Paragraph("• Standard Shipping (5-7 business days) - Free", styles['BodyText']))
    story.append(Paragraph("• Express Shipping (2-3 business days) - $15", styles['BodyText']))
    story.append(Paragraph("• Overnight Shipping (1 business day) - $35", styles['BodyText']))
    story.append(Paragraph(
        "International shipping available to most countries with rates calculated at checkout.",
        body_justified
    ))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("<b>Support Hours</b>", styles['Heading2']))
    story.append(Paragraph("Our support team is available:", body_justified))
    story.append(Paragraph("• Monday-Friday: 8 AM - 8 PM EST", styles['BodyText']))
    story.append(Paragraph("• Saturday: 10 AM - 4 PM EST", styles['BodyText']))
    story.append(Paragraph("• Sunday: Closed", styles['BodyText']))
    story.append(Paragraph("Emergency support available 24/7 for Enterprise customers.", body_justified))

    story.append(PageBreak())

    story.append(Paragraph("<b>Privacy Policy</b>", styles['Heading2']))
    story.append(Paragraph(
        "We take your privacy seriously. We collect only the information necessary to "
        "provide our services and never sell your data to third parties. All data is "
        "encrypted in transit and at rest. For more details, see our full privacy "
        "policy at company.com/privacy.",
        body_justified
    ))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("<b>Account Management</b>", styles['Heading2']))
    story.append(Paragraph(
        "You can manage your account settings at any time by logging into your dashboard. "
        "This includes updating billing information, changing your plan, and managing team members. "
        "To close your account, contact our support team.",
        body_justified
    ))

    doc.build(story)
    print("✅ Created knowledge_base.pdf (2 pages)")


def generate_test_image():
    """Generate a simple test image."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("❌ Pillow not installed. Installing...")
        import subprocess
        subprocess.check_call(["pip", "install", "Pillow"])
        from PIL import Image, ImageDraw, ImageFont

    print("\n🖼️  Generating test_image.png...")

    img = Image.new('RGB', (800, 600), color='white')
    draw = ImageDraw.Draw(img)

    # Title
    draw.text((280, 50), "Sales Chart 2025", fill='black')

    # Draw simple bar chart
    bars = [
        ('Q1', 240, 'blue'),
        ('Q2', 310, 'green'),
        ('Q3', 280, 'orange'),
        ('Q4', 350, 'red'),
    ]

    x_start = 150
    y_base = 500
    bar_width = 100

    for i, (label, height, color) in enumerate(bars):
        x = x_start + i * 150
        y = y_base - height

        draw.rectangle([x, y, x + bar_width, y_base], fill=color, outline='black')
        draw.text((x + 20, y_base + 20), label, fill='black')
        draw.text((x + 20, y - 30), f"${height}K", fill='black')

    draw.line([(100, y_base), (700, y_base)], fill='black', width=2)
    draw.line([(100, y_base), (100, 150)], fill='black', width=2)

    img.save("test_image.png")
    print("✅ Created test_image.png")


if __name__ == "__main__":
    print("\n" + "📦 "*30)
    print("GENERATING TEST FILES FOR MULTIMODAL TESTS")
    print("📦 "*30 + "\n")

    generate_test_pdfs()
    generate_test_image()

    print("\n" + "✅ "*30)
    print("ALL TEST FILES GENERATED")
    print("✅ "*30)

    print("\nGenerated files:")
    files = [
        ("multi-page-with-tables.pdf", "3 pages - Annual business report"),
        ("invoice.pdf", "1 page - Sample invoice"),
        ("report_q1.pdf", "1 page - Q1 financial report"),
        ("report_q2.pdf", "1 page - Q2 financial report"),
        ("knowledge_base.pdf", "2 pages - Company policies"),
        ("test_image.png", "Bar chart image"),
    ]

    for f, desc in files:
        if Path(f).exists():
            size = Path(f).stat().st_size
            print(f"  ✓ {f:25s} ({size:6,} bytes) - {desc}")
        else:
            print(f"  ✗ {f:25s} (not found)")

    print("\n💡 Couple of resources for my multimodal test")