from flask import Flask, request, jsonify, send_file, redirect, url_for
from flask_cors import CORS
import mysql.connector
import logging
import google.generativeai as genai
import re
import time
from datetime import datetime
import csv
from io import StringIO, BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib import colors
from reportlab.lib.units import inch
import matplotlib.pyplot as plt
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import os
from tempfile import NamedTemporaryFile

# Configure logging
logging.basicConfig(filename='tax_assistant.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR PASSWORD HERE',
    'database': 'taxie'
}

GEMINI_API_KEY = "YOUR GEMINI API HERE"

# Gmail SMTP Config
GMAIL_SMTP = "smtp.gmail.com"
GMAIL_PORT = 587
GMAIL_SENDER = "YOUR GMAIL HERE"
GMAIL_PASSWORD = "YOUR APP PASSWORD HERE"

# Tax slabs for different years
TAX_SLABS_BY_YEAR = {
    2025: [
        (600000, 0.0, 0),
        (1200000, 0.05, 0),
        (2400000, 0.15, 30000),
        (3600000, 0.25, 210000),
        (float('inf'), 0.30, 510000)
    ],
    2024: [
        (600000, 0.0, 0),
        (1200000, 0.05, 0),
        (2200000, 0.15, 30000),
        (3200000, 0.25, 165000),
        (float('inf'), 0.35, 565000)
    ],
    2012: [
        (350000, 0.0, 0),
        (400000, 0.5, 0),
        (500000, 1.0, 0),
        (750000, 2.0, 0),
        (1000000, 3.5, 0),
        (3000000, 5.0, 0),
        (float('inf'), 7.5, 0)
    ],
    2002: [
        (300000, 0.0, 0),
        (600000, 0.075, 0),
        (1200000, 0.15, 22500),
        (float('inf'), 0.25, 97500)
    ]
}

app = Flask(__name__, static_folder="C:\\Taxie App\\static")
CORS(app)

def ask_gemini(prompt, max_retries=3):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 1000
                }
            )
            if response.text:
                logging.info("Gemini API call successful")
                return response.text
            else:
                logging.warning(f"No valid response from Gemini. Attempt {attempt + 1}/{max_retries}")
        except Exception as e:
            logging.error(f"Gemini API error, attempt {attempt + 1}/{max_retries}: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5 * (2 ** attempt))
                continue
    logging.error("Gemini API failed after all retries")
    return None

def send_email_with_report(recipient, user, pdf_buffer, csv_buffer):
    try:
        msg = MIMEMultipart()
        msg['From'] = GMAIL_SENDER
        msg['To'] = recipient
        msg['Subject'] = f"Your Tax Report from Taxie - {datetime.now().strftime('%Y-%m-%d')}"

        html = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border-radius: 10px; background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);">
                    <div style="text-align: center; margin-bottom: 20px;">
                        <h1 style="color: #4b6cb7;">Your Tax Report is Ready! 💰</h1>
                        <p style="font-size: 16px;">Hello {user['name'].title()},</p>
                        <p style="font-size: 16px;">Thank you for using Taxie, your personal tax assistant. Please find your detailed tax report attached.</p>
                    </div>
                    
                    <div style="background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                        <h2 style="color: #4b6cb7; border-bottom: 2px solid #4b6cb7; padding-bottom: 5px;">Quick Summary</h2>
                        <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                            <tr>
                                <td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>Annual Income:</strong></td>
                                <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">PKR {user['annual_income']:,.2f}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>Taxable Income:</strong></td>
                                <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">PKR {user['taxable_income']:,.2f}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>Tax Due:</strong></td>
                                <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">PKR {user['tax_due']:,.2f}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px;"><strong>Filer Status:</strong></td>
                                <td style="padding: 8px; text-align: right;">{user['filer_status'].title()}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <div style="text-align: center; margin-top: 20px;">
                        <p style="font-size: 14px; color: #666;">You'll find two attachments with this email:</p>
                        <ul style="list-style-type: none; padding: 0; margin: 10px 0;">
                            <li style="margin-bottom: 5px;">📄 <strong>Tax_Report.pdf</strong> - Detailed breakdown with charts</li>
                            <li>📊 <strong>Tax_Data.csv</strong> - Raw data for your records</li>
                        </ul>
                    </div>
                    
                    <div style="text-align: center; margin-top: 30px; color: #666; font-size: 12px;">
                        <p>This email was sent automatically by Taxie - Your Personal Tax Assistant</p>
                        <p>Need help? Reply to this email and we'll assist you!</p>
                    </div>
                </div>
            </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))

        pdf_attachment = MIMEApplication(pdf_buffer.read(), _subtype="pdf")
        pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f"Tax_Report_{user['name']}.pdf")
        msg.attach(pdf_attachment)

        csv_attachment = MIMEApplication(csv_buffer.getvalue().encode('utf-8'), _subtype="csv")
        csv_attachment.add_header('Content-Disposition', 'attachment', filename=f"Tax_Data_{user['name']}.csv")
        msg.attach(csv_attachment)

        with smtplib.SMTP(GMAIL_SMTP, GMAIL_PORT) as server:
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.send_message(msg)
        
        logging.info(f"Email sent successfully to {recipient}")
        return True
    except Exception as e:
        logging.error(f"Failed to send email: {str(e)}")
        return False

def generate_tax_chart(user, taxable_income, tax_due, total_deductions):
    try:
        plt.figure(figsize=(8, 6))
        
        # Pie chart for income breakdown
        labels = ['Tax Due', 'Deductions', 'Net Income']
        sizes = [tax_due, total_deductions, max(0, user['annual_income'] - tax_due - total_deductions)]
        colors = ['#ff6b6b', '#4ecdc4', '#45aaf2']
        explode = (0.1, 0, 0)
        
        if sum(sizes) == 0 or all(s == 0 for s in sizes):
            sizes = [1, 1, 1]
            labels = ['No Tax', 'No Deductions', 'No Income']
        
        plt.pie(sizes, explode=explode, labels=labels, colors=colors, autopct='%1.1f%%',
                shadow=True, startangle=140, textprops={'fontsize': 10})
        plt.title(f'Income Breakdown ({user["tax_year"]})', fontweight='bold', pad=20)
        
        plt.tight_layout()
        
        img_buffer = BytesIO()
        plt.savefig(img_buffer, format='png', dpi=120, bbox_inches='tight')
        img_buffer.seek(0)
        plt.close()
        
        return img_buffer
    except Exception as e:
        logging.error(f"Error generating chart: {e}")
        return None

def generate_pdf_report(user, taxable_income, tax_due, total_deductions):
    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, 
                              rightMargin=inch/2, leftMargin=inch/2,
                              topMargin=inch/2, bottomMargin=inch/2)
        
        styles = getSampleStyleSheet()
        
        styles.add(ParagraphStyle(name='TitleStyle', 
                                fontSize=18, 
                                leading=22, 
                                alignment=TA_CENTER,
                                textColor=colors.HexColor('#4b6cb7'),
                                spaceAfter=20))
        
        styles.add(ParagraphStyle(name='Heading1Style',
                                fontSize=14,
                                leading=18,
                                textColor=colors.HexColor('#4b6cb7'),
                                spaceBefore=12,
                                spaceAfter=6))
        
        styles.add(ParagraphStyle(name='Heading2Style',
                                fontSize=12,
                                leading=16,
                                textColor=colors.HexColor('#4b6cb7'),
                                spaceBefore=10,
                                spaceAfter=4))
        
        styles.add(ParagraphStyle(name='NormalStyle',
                                fontSize=10,
                                leading=14,
                                spaceAfter=6))
        
        styles.add(ParagraphStyle(name='TableHeaderStyle',
                                fontSize=10,
                                leading=14,
                                textColor=colors.white,
                                backColor=colors.HexColor('#4b6cb7')))
        
        elements = []
        
        elements.append(Paragraph(f"TAX REPORT - {user['name'].upper()} ({user['tax_year']})", styles['TitleStyle']))
        elements.append(Spacer(1, 12))
        
        header_data = [
            ['Tax Year', 'Filer Status', 'Employment Type'],
            [str(user['tax_year']), user['filer_status'].title(), user['employment_type'].title()]
        ]
        
        header_table = Table(header_data, colWidths=[doc.width/3]*3)
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4b6cb7')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,1), colors.HexColor('#f5f7fa')),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#c3cfe2'))
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 20))
        
        elements.append(Paragraph("Key Figures", styles['Heading1Style']))
        
        figures_data = [
            ['Annual Income', f"PKR {user['annual_income']:,.2f}"],
            ['Total Deductions', f"PKR {total_deductions:,.2f}"],
            ['Taxable Income', f"PKR {taxable_income:,.2f}"],
            ['Tax Due', f"PKR {tax_due:,.2f}"]
        ]
        
        figures_table = Table(figures_data, colWidths=[doc.width/2]*2)
        figures_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#e0e0e0'))
        ]))
        elements.append(figures_table)
        elements.append(Spacer(1, 20))
        
        chart_img = generate_tax_chart(user, taxable_income, tax_due, total_deductions)
        if chart_img:
            elements.append(Paragraph("Visual Summary", styles['Heading1Style']))
            elements.append(Image(chart_img, width=6*inch, height=3*inch))
            elements.append(Spacer(1, 20))
        
        elements.append(Paragraph("Detailed Breakdown", styles['Heading1Style']))
        
        elements.append(Paragraph("Tax Slabs Applied", styles['Heading2Style']))
        
        tax_slabs = TAX_SLABS_BY_YEAR.get(user['tax_year'], TAX_SLABS_BY_YEAR[2025])
        slab_data = [['Income Range', 'Rate', 'Fixed Tax', 'Amount']]
        prev_limit = 0
        total_tax = 0
        
        for limit, rate, fixed_tax in tax_slabs:
            if taxable_income > prev_limit:
                slab_amount = min(taxable_income, limit) - prev_limit
                if slab_amount > 0:
                    tax_amount = slab_amount * rate + fixed_tax
                    total_tax += tax_amount
                    slab_data.append([
                        f"PKR {prev_limit:,.2f} - PKR {min(taxable_income, limit):,.2f}",
                        f"{rate*100}%",
                        f"PKR {fixed_tax:,.2f}",
                        f"PKR {tax_amount:,.2f}"
                    ])
            prev_limit = limit
        
        slab_table = Table(slab_data, colWidths=[doc.width/4]*4)
        slab_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4b6cb7')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f5f7fa')),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#c3cfe2'))
        ]))
        elements.append(slab_table)
        elements.append(Spacer(1, 12))
        
        # Ensure "Deductions Claimed" heading and table stay on the same page
        elements.append(Paragraph("Deductions Claimed", styles['Heading2Style']))
        deductions_data = [
            ['Deduction Type', 'Amount', 'Max Allowed'],
            ['Zakat Paid', f"PKR {user['zakat_paid']:,.2f}", "100% of amount"],
            ['Dependents', f"PKR {min(user['num_dependents'], 4) * 50000:,.2f}", f"{min(user['num_dependents'], 4)} x PKR 50,000"],
        ]
        if user.get('rent_paid', 0) > 0:
            deductions_data.append(['Rent Paid', f"PKR {min(user['rent_paid'], user['annual_income'] * 0.25):,.2f}", "25% of income"])
        deductions_data.extend([
            ['Medical Expenses', f"PKR {min(user['medical_expenses'], 100000):,.2f}", "PKR 100,000"],
            ['Charitable Donations', f"PKR {min(user['charitable_donations'], 50000):,.2f}", "PKR 50,000"]
        ])
        if user['employment_type'] in ['business', 'freelancer']:
            deductions_data.append([
                'Business Expenses', 
                f"PKR {min(user['business_expenses'], user['annual_income'] * 0.5):,.2f}", 
                "50% of income"
            ])
        
        deductions_table = Table(deductions_data, colWidths=[doc.width/3]*3, repeatRows=1)
        deductions_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4b6cb7')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f5f7fa')),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#c3cfe2'))
        ]))
        # Force the heading and table to stay together
        elements.append(deductions_table)
        elements.append(Spacer(1, 20))
        
        elements.append(Paragraph("Generated by Taxie - Your Personal Tax Assistant", 
                               ParagraphStyle(name='FooterStyle',
                                            fontSize=8,
                                            alignment=TA_CENTER,
                                            textColor=colors.grey)))
        elements.append(Paragraph(f"Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 
                               ParagraphStyle(name='FooterDateStyle',
                                            fontSize=7,
                                            alignment=TA_CENTER,
                                            textColor=colors.grey)))
        
        doc.build(elements)
        buffer.seek(0)
        return buffer
    except Exception as e:
        logging.error(f"Error generating PDF: {e}")
        return None

def generate_csv_report(user, taxable_income, tax_due, total_deductions):
    try:
        output = StringIO()
        writer = csv.writer(output)
        
        writer.writerow(["Tax Report for", user['name']])
        writer.writerow(["Generated on", datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        writer.writerow([])
        
        writer.writerow(["Basic Information"])
        writer.writerow(["Field", "Value"])
        writer.writerow(["Name", user['name']])
        writer.writerow(["Email", user['gmail']])
        writer.writerow(["Tax Year", user['tax_year']])
        writer.writerow(["Filer Status", user['filer_status']])
        writer.writerow(["Employment Type", user['employment_type']])
        writer.writerow([])
        
        writer.writerow(["Financial Summary"])
        writer.writerow(["Annual Income", f"PKR {user['annual_income']:,.2f}"])
        writer.writerow(["Total Deductions", f"PKR {total_deductions:,.2f}"])
        writer.writerow(["Taxable Income", f"PKR {taxable_income:,.2f}"])
        writer.writerow(["Tax Due", f"PKR {tax_due:,.2f}"])
        writer.writerow([])
        
        writer.writerow(["Tax Slabs Applied"])
        writer.writerow(["Income Range", "Rate", "Fixed Tax", "Amount"])
        tax_slabs = TAX_SLABS_BY_YEAR.get(user['tax_year'], TAX_SLABS_BY_YEAR[2025])
        prev_limit = 0
        for limit, rate, fixed_tax in tax_slabs:
            if taxable_income > prev_limit:
                slab_amount = min(taxable_income, limit) - prev_limit
                if slab_amount > 0:
                    writer.writerow([
                        f"PKR {prev_limit:,.2f} - PKR {min(taxable_income, limit):,.2f}",
                        f"{rate*100}%",
                        f"PKR {fixed_tax:,.2f}",
                        f"PKR {slab_amount * rate + fixed_tax:,.2f}"
                    ])
            prev_limit = limit
        writer.writerow([])
        
        writer.writerow(["Deductions Breakdown"])
        writer.writerow(["Deduction Type", "Amount", "Max Allowed"])
        writer.writerow(["Zakat Paid", f"PKR {user['zakat_paid']:,.2f}", "100% of amount"])
        writer.writerow(["Dependents", f"PKR {min(user['num_dependents'], 4) * 50000:,.2f}", f"{min(user['num_dependents'], 4)} x PKR 50,000"])
        if user.get('rent_paid', 0) > 0:
            writer.writerow(["Rent Paid", f"PKR {min(user['rent_paid'], user['annual_income'] * 0.25):,.2f}", "25% of income"])
        writer.writerow(["Medical Expenses", f"PKR {min(user['medical_expenses'], 100000):,.2f}", "PKR 100,000"])
        writer.writerow(["Charitable Donations", f"PKR {min(user['charitable_donations'], 50000):,.2f}", "PKR 50,000"])
        if user['employment_type'] in ['business', 'freelancer']:
            writer.writerow([
                "Business Expenses", 
                f"PKR {min(user['business_expenses'], user['annual_income'] * 0.5):,.2f}", 
                "50% of income"
            ])
        
        output.seek(0)
        return output
    except Exception as e:
        logging.error(f"Error generating CSV: {e}")
        return None

@app.route("/")
def index():
    # Redirect to one.html in the static folder located at C:\Taxie App\static
    return redirect(url_for('static', filename='one.html'))

@app.route("/generate-report", methods=["POST"])
def generate_report():
    try:
        data = request.get_json()
        user = data.get("user")
        
        if not user:
            return jsonify({"error": "User data not provided"}), 400
        
        pdf_buffer = generate_pdf_report(user, user['taxable_income'], user['tax_due'], 
                                       user['annual_income'] - user['taxable_income'])
        csv_buffer = generate_csv_report(user, user['taxable_income'], user['tax_due'], 
                                      user['annual_income'] - user['taxable_income'])
        
        if not pdf_buffer or not csv_buffer:
            return jsonify({"error": "Failed to generate reports"}), 500
        
        email_sent = send_email_with_report(
            user['gmail'], 
            user, 
            pdf_buffer, 
            csv_buffer
        )
        
        if not email_sent:
            return jsonify({"error": "Failed to send email"}), 500
        
        return jsonify({
            "message": "Report generated and sent successfully",
            "email_sent": email_sent
        })
    except Exception as e:
        logging.error(f"Report generation error: {e}")
        return jsonify({"error": str(e)}), 500

def fallback_tax_explanation(user, taxable_income, tax_due, total_deductions):
    explanation = f"Hey {user['name'].title()}, let's break down your taxes for {user['tax_year']} like we're chatting over coffee! ☕\n\n"
    explanation += f"- Your Income: You earned PKR {user['annual_income']:,.2f}. Great job! 🌟\n"
    explanation += f"- Deductions: Here's how we saved you some cash:\n"
    explanation += f"  - Zakat: PKR {user['zakat_paid']:,.2f} (all counts).\n"
    explanation += f"  - Dependents: {user['num_dependents']} at PKR 50,000 each = PKR {min(user['num_dependents'], 4) * 50000:,.2f} (capped at 4).\n"
    if user.get('rent_paid', 0) > 0:
        explanation += f"  - Rent: PKR {min(user['rent_paid'], user['annual_income'] * 0.25):,.2f} (max 25% of income).\n"
    explanation += f"  - Medical Expenses: PKR {min(user['medical_expenses'], 100000):,.2f} (up to PKR 100,000).\n"
    explanation += f"  - Donations: PKR {min(user['charitable_donations'], 50000):,.2f} (up to PKR 50,000).\n"
    if user['employment_type'] in ['business', 'freelancer']:
        explanation += f"  - Business Expenses: PKR {min(user['business_expenses'], user['annual_income'] * 0.5):,.2f} (max 50% of income).\n"
    explanation += f"- Total Deductions: PKR {total_deductions:,.2f}. Nice savings! 😊\n"
    explanation += f"- Taxable Income: Income - Deductions = PKR {taxable_income:,.2f}.\n"
    explanation += f"- Tax Slabs ({user['tax_year']}):\n"
    tax_slabs = TAX_SLABS_BY_YEAR.get(user['tax_year'], TAX_SLABS_BY_YEAR[2025])
    prev_limit = 0
    for limit, rate, fixed_tax in tax_slabs:
        if taxable_income > prev_limit and limit != float('inf'):
            slab_amount = min(taxable_income, limit) - prev_limit
            if slab_amount > 0:
                explanation += f"  - PKR {prev_limit:,.2f} to PKR {min(taxable_income, limit):,.2f}: {rate*100:.0f}%"
                if fixed_tax > 0:
                    explanation += f" + PKR {fixed_tax:,.2f}"
                explanation += ".\n"
        prev_limit = limit
    if taxable_income <= 1000000 and user['filer_status'] == 'filer':
        explanation += f"- Rebate: PKR 12,500 off for filers with income below PKR 1,000,000. Sweet deal! 🎉\n"
    if user['employment_type'] in ['business', 'freelancer']:
        explanation += f"- Business Tax: Compared income tax (PKR {tax_due:,.2f}) with turnover tax (1.5% of PKR {user['business_turnover']:,.2f} = PKR {user['business_turnover'] * 0.015:,.2f}). Took the higher one.\n"
    explanation += f"- Total Tax Due: PKR {tax_due:,.2f}.\n"
    explanation += f"\nThat's it, {user['name'].title()}! Your taxes are sorted! 🌟"
    return explanation

def fallback_tax_recommendations(user):
    recommendations = f"Yo {user['name'].title()}, wanna save more on taxes in {user['tax_year']}? Here's some cool ideas! 😎\n\n"
    if user['filer_status'] == 'non-filer':
        recommendations += f"- Join the Filer Club: Non-filers face double withholding taxes. Sign up with FBR to save! 🚀\n"
    if user['zakat_paid'] == 0:
        recommendations += f"- Give Zakat: It lowers your taxable income directly. Try 2.5% of savings above nisab! 🙏\n"
    if user['num_dependents'] < 4:
        remaining = 4 - user['num_dependents']
        recommendations += f"- Add Family: Claimed {user['num_dependents']} dependent(s). Add up to {remaining} more for PKR {remaining * 50000:,.2f} in deductions. 🥰\n"
    if user.get('rent_paid', 0) < user['annual_income'] * 0.25:
        remaining = user['annual_income'] * 0.25 - user.get('rent_paid', 0)
        recommendations += f"- Claim More Rent: Reported PKR {user.get('rent_paid', 0):,.2f}, but you can deduct up to PKR {user['annual_income'] * 0.25:,.2f}. Add PKR {remaining:,.2f} if you paid it! 🏠\n"
    if user['medical_expenses'] < 100000:
        recommendations += f"- Save Medical Receipts: Claimed PKR {user['medical_expenses']:,.2f}. Keep receipts up to PKR 100,000 for meds or visits. 🩺\n"
    if user['charitable_donations'] < 50000:
        recommendations += f"- Donate More: Gave PKR {user['charitable_donations']:,.2f}. Donate up to PKR 50,000 to FBR-approved charities! ❤️\n"
    if user['employment_type'] in ['business', 'freelancer']:
        recommendations += f"- Track Business Costs: Claimed PKR {user['business_expenses']:,.2f}. Log expenses up to PKR {user['annual_income'] * 0.5:,.2f} to cut taxes! 💼\n"
    recommendations += f"\nYou're killing it, {user['name'].title()}! Chat with a tax pro for more tricks. 😜"
    return recommendations

def create_database_and_table():
    try:
        conn = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password']
        )
        cursor = conn.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS taxie")
        cursor.execute("USE taxie")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tax_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255),
                gmail VARCHAR(255),
                annual_income DECIMAL(30,2),
                zakat_paid DECIMAL(30,2),
                num_dependents INT,
                rent_paid DECIMAL(30,2),
                medical_expenses DECIMAL(30,2),
                charitable_donations DECIMAL(30,2),
                employment_type VARCHAR(50),
                business_expenses DECIMAL(30,2),
                business_turnover DECIMAL(30,2),
                filer_status VARCHAR(20),
                taxable_income DECIMAL(30,2),
                tax_due DECIMAL(30,2),
                tax_year INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        logging.info("Database and table created successfully")
    except mysql.connector.Error as e:
        logging.error(f"Database error: Failed to create database/table: {e}")
        raise
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def calculate_tax(user):
    total_deductions = user['zakat_paid']
    total_deductions += min(user['num_dependents'], 4) * 50000
    if user.get('rent_paid', 0) > 0:
        total_deductions += min(user['rent_paid'], user['annual_income'] * 0.25)
    total_deductions += min(user['medical_expenses'], 100000)
    total_deductions += min(user['charitable_donations'], 50000)
    if user['employment_type'] in ['business', 'freelancer']:
        total_deductions += min(user['business_expenses'], user['annual_income'] * 0.5)
    
    taxable_income = max(user['annual_income'] - total_deductions, 0)
    
    tax_slabs = TAX_SLABS_BY_YEAR.get(user['tax_year'], TAX_SLABS_BY_YEAR[2025])
    if user['tax_year'] not in TAX_SLABS_BY_YEAR:
        logging.warning(f"Tax year {user['tax_year']} not found. Using default 2025 slabs.")
    
    tax_due = 0
    prev_limit = 0
    for limit, rate, fixed_tax in tax_slabs:
        slab_amount = min(taxable_income, limit) - prev_limit
        if slab_amount > 0:
            tax_due += slab_amount * rate + fixed_tax
        prev_limit = limit
        if taxable_income <= limit:
            break
    
    if taxable_income <= 1000000 and user['filer_status'] == 'filer':
        tax_due = max(0, tax_due - 12500)
    
    if user['employment_type'] in ['business', 'freelancer']:
        turnover_tax = user['business_turnover'] * 0.015
        tax_due = max(tax_due, turnover_tax)
    
    gemini_validation = ask_gemini(
        f"Validate this tax calculation for Pakistan's {user['tax_year']} tax year. Details:\n"
        f"- Annual Income: PKR {user['annual_income']:,.2f}\n"
        f"- Total Deductions: PKR {total_deductions:,.2f}\n"
        f"- Taxable Income: PKR {taxable_income:,.2f}\n"
        f"- Calculated Tax Due: PKR {tax_due:,.2f}\n"
        f"- Filer Status: {user['filer_status']}\n"
        f"- Employment Type: {user['employment_type']}\n"
        f"Return 'Valid' if correct, or suggest a corrected tax due amount."
    )
    
    if gemini_validation and gemini_validation != 'Valid':
        try:
            corrected_tax = float(re.search(r'PKR (\d+\.?\d*)', gemini_validation).group(1))
            logging.info(f"Gemini suggested corrected tax: PKR {corrected_tax}")
            tax_due = corrected_tax
        except:
            logging.warning("Could not parse Gemini's corrected tax amount. Using calculated value.")
    
    return round(taxable_income, 2), round(tax_due, 2), round(total_deductions, 2)

def save_to_database(user, taxable_income, tax_due):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        query = """
            INSERT INTO tax_records
            (name, gmail, annual_income, zakat_paid, num_dependents, rent_paid, medical_expenses,
            charitable_donations, employment_type, business_expenses, business_turnover,
            filer_status, taxable_income, tax_due, tax_year)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            user['name'], user['gmail'], user['annual_income'], user['zakat_paid'], user['num_dependents'],
            user.get('rent_paid', 0), user['medical_expenses'], user['charitable_donations'],
            user['employment_type'], user['business_expenses'], user['business_turnover'],
            user['filer_status'], taxable_income, tax_due, user['tax_year']
        )
        cursor.execute(query, params)
        conn.commit()
        logging.info(f"Tax record saved for {user['name']}")
        return cursor.lastrowid
    except mysql.connector.Error as e:
        logging.error(f"Database error: Failed to save record: {e}")
        return None
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    session = data.get("session", {})
    answer = data.get("message", "").strip()
    step = session.get("step", 0)
    user = session.get("user", {})

    if step > 0 and "last_key" in session:
        last_q = next(q for q in questions if q["key"] == session["last_key"])
        try:
            value = answer
            if last_q.get("cast"):
                value = last_q["cast"](answer)
            if last_q.get("validator") and not last_q["validator"](answer):
                return jsonify({
                    "reply": last_q["error"],
                    "session": session
                })
            user[last_q["key"]] = value
        except Exception:
            return jsonify({
                "reply": last_q["error"],
                "session": session
            })

    while step < len(questions):
        q = questions[step]
        if q.get("conditional") and not q["conditional"](user):
            step += 1
            continue
        session["last_key"] = q["key"]
        session["step"] = step + 1
        session["user"] = user
        return jsonify({
            "reply": q["question"],
            "session": session
        })
    
    create_database_and_table()
    if user.get("employment_type") not in ["business", "freelancer"]:
        user["business_expenses"] = 0.0
        user["business_turnover"] = 0.0
    user["annual_income"] = float(user["annual_income"])
    user["zakat_paid"] = float(user["zakat_paid"])
    user["num_dependents"] = int(user["num_dependents"])
    user["medical_expenses"] = float(user["medical_expenses"])
    user["charitable_donations"] = float(user["charitable_donations"])
    user["business_expenses"] = float(user.get("business_expenses", 0.0))
    user["business_turnover"] = float(user.get("business_turnover", 0.0))
    user["tax_year"] = int(user["tax_year"])
    user["taxable_income"], user["tax_due"], total_deductions = calculate_tax(user)
    record_id = save_to_database(user, user["taxable_income"], user["tax_due"])
    
    explanation = ask_gemini(
        f"Yo! I'm a super friendly tax assistant for Pakistan's {user['tax_year']} tax year. Explain in simple, fun language how we calculated this person's taxes. Use bullet points, keep it short and chill, like we're chatting over chai. Details:\n"
        f"- Name: {user['name']}\n"
        f"- Gmail: {user['gmail']}\n"
        f"- Annual Income: PKR {user['annual_income']:,.2f}\n"
        f"- Zakat Paid: PKR {user['zakat_paid']:,.2f}\n"
        f"- Dependents: {user['num_dependents']}\n"
        f"- Rent Paid: PKR {user.get('rent_paid', 0):,.2f}\n"
        f"- Medical Expenses: PKR {user['medical_expenses']:,.2f}\n"
        f"- Charitable Donations: PKR {user['charitable_donations']:,.2f}\n"
        f"- Employment Type: {user['employment_type'].title()}\n"
        f"- Business Expenses: PKR {user['business_expenses']:,.2f}\n"
        f"- Business Turnover: PKR {user['business_turnover']:,.2f}\n"
        f"- Filer Status: {user['filer_status'].title()}\n"
        f"- Taxable Income: PKR {user['taxable_income']:,.2f}\n"
        f"- Tax Due: PKR {user['tax_due']:,.2f}\n"
        f"Make it warm and fun, like talking to a friend!"
    ) or fallback_tax_explanation(user, user["taxable_income"], user["tax_due"], total_deductions)
    
    recommendations = ask_gemini(
        f"Hi! I'm a chill tax assistant for Pakistan's {user['tax_year']} tax year. Give 3-5 practical, tailored tax-saving tips for this person. Use bullet points, keep it short, upbeat, and personal, like we're chatting over coffee. Details:\n"
        f"- Name: {user['name']}\n"
        f"- Gmail: {user['gmail']}\n"
        f"- Annual Income: PKR {user['annual_income']:,.2f}\n"
        f"- Zakat Paid: PKR {user['zakat_paid']:,.2f}\n"
        f"- Dependents: {user['num_dependents']}\n"
        f"- Rent Paid: PKR {user.get('rent_paid', 0):,.2f}\n"
        f"- Medical Expenses: PKR {user['medical_expenses']:,.2f}\n"
        f"- Charitable Donations: PKR {user['charitable_donations']:,.2f}\n"
        f"- Employment Type: {user['employment_type'].title()}\n"
        f"- Business Expenses: PKR {user['business_expenses']:,.2f}\n"
        f"- Business Turnover: PKR {user['business_turnover']:,.2f}\n"
        f"- Filer Status: {user['filer_status'].title()}\n"
        f"Focus on easy, legal ways to save taxes in Pakistan, based on their info."
    ) or fallback_tax_recommendations(user)
    
    reply = (
        f"🎉 Here's your tax summary for {user['tax_year']}, {user['name'].title()}!\n"
        f"Taxable Income: PKR {user['taxable_income']:,.2f}\n"
        f"Tax Due: PKR {user['tax_due']:,.2f}\n\n"
        f"--- How We Got Your Numbers ---\n{explanation}\n\n"
        f"--- Tricks to Save on Taxes ---\n{recommendations}"
    )
    session["completed"] = True
    session["user"] = user
    
    return jsonify({
        "reply": reply,
        "session": session,
        "report_available": True,
        "user": user
    })

questions = [
    {"key": "name", "question": "🌟 What's your name, buddy? 😊"},
    {"key": "gmail", "question": "📧 What's your Gmail address? (e.g. anumta@gmail.com)", "validator": lambda x: bool(re.match(r'^[a-zA-Z0-9._%+-]+@gmail\.com$', x)), "error": "Please use a valid Gmail address."},
    {"key": "employment_type", "question": "💼 What's your job vibe? (salaried, business, freelancer, other)", "validator": lambda x: x.lower() in ['salaried', 'business', 'freelancer', 'other'], "error": "Try 'salaried', 'business', 'freelancer', or 'other'."},
    {"key": "annual_income", "question": "💰 How much did you make this year (in PKR)?", "cast": float, "validator": lambda x: float(x) >= 0, "error": "Use a non-negative number."},
    {"key": "zakat_paid", "question": "🙏 Did you give any zakat this year? (PKR)", "cast": float, "validator": lambda x: float(x) >= 0, "error": "Enter a non-negative number."},
    {"key": "num_dependents", "question": "🥰 How many dependents (family members) do you have?", "cast": int, "validator": lambda x: int(x) >= 0, "error": "Enter 0 or a positive number."},
    {"key": "home_ownership", "question": "🏡 Do you rent your house or own it? (rent/own)", "validator": lambda x: x.lower() in ['rent', 'own'], "error": "Please enter 'rent' or 'own'."},
    {"key": "rent_paid", "question": "🏠 How much rent did you pay this year (in PKR)?", "cast": float, "validator": lambda x: float(x) >= 0, "error": "Enter a non-negative number.", "conditional": lambda u: u.get("home_ownership", "").lower() == "rent"},
    {"key": "medical_expenses", "question": "🩺 Any medical expenses this year (in PKR)?", "cast": float, "validator": lambda x: float(x) >= 0, "error": "Enter a non-negative number."},
    {"key": "charitable_donations", "question": "❤️ Charitable donations to FBR-approved charities (in PKR)?", "cast": float, "validator": lambda x: float(x) >= 0, "error": "Enter a non-negative number."},
    {"key": "business_expenses", "question": "💼 Any business expenses this year (in PKR)? (0 if not applicable)", "cast": float, "validator": lambda x: float(x) >= 0, "error": "Enter a non-negative number.", "conditional": lambda u: u.get("employment_type") in ["business", "freelancer"]},
    {"key": "business_turnover", "question": "📊 What's your business turnover (total revenue) in PKR? (0 if not applicable)", "cast": float, "validator": lambda x: float(x) >= 0, "error": "Enter a non-negative number.", "conditional": lambda u: u.get("employment_type") in ["business", "freelancer"]},
    {"key": "filer_status", "question": "📋 Are you an FBR filer or non-filer?", "validator": lambda x: x.lower() in ['filer', 'non-filer'], "error": "Type 'filer' or 'non-filer'."},
    {"key": "tax_year", "question": "📅 Which tax year? (e.g. 2024)", "cast": int, "validator": lambda x: 2000 < int(x) <= 2025, "error": "Enter a valid year between 2001 and 2025."}
]

if __name__ == "__main__":
    print("Taxie is running! Open your browser and navigate to: http://127.0.0.1:5000/")
    app.run(debug=True)