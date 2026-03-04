from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import pandas as pd
import smtplib
import dns.resolver
import re
import asyncio
import io
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate
import logging
import time
import base64
import ssl

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Email Marketing API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== HEALTH CHECK =====================
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Server is running"}

# ===================== EMAIL VALIDATION =====================
def validate_email_syntax(email: str) -> dict:
    if not email:
        return {"valid": False, "reason": "Empty email"}
    
    email = email.strip().lower()
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if not re.match(pattern, email):
        return {"valid": False, "reason": "Invalid format"}
    
    try:
        domain = email.split('@')[1]
        return {"valid": True, "domain": domain}
    except:
        return {"valid": False, "reason": "No @ symbol"}

def check_mx_record(domain: str) -> bool:
    try:
        dns.resolver.resolve(domain, 'MX')
        return True
    except:
        return False

@app.post("/api/validate")
async def validate_emails(
    file: UploadFile = File(...),
    email_column: str = Form("email")
):
    try:
        contents = await file.read()
        logger.info(f"Processing file: {file.filename}")
        
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        elif file.filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            content = contents.decode('utf-8')
            emails = [line.strip() for line in content.split('\n') if line.strip()]
            df = pd.DataFrame({email_column: emails})
        
        if email_column not in df.columns:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": f"Column '{email_column}' not found"}
            )
        
        emails = df[email_column].dropna().unique().tolist()
        emails = [str(e).lower().strip() for e in emails if '@' in str(e)]
        
        results = []
        valid_count = invalid_count = risky_count = 0
        
        for email in emails[:100]:
            validation = validate_email_syntax(email)
            
            if validation["valid"]:
                has_mx = check_mx_record(validation["domain"])
                if has_mx:
                    results.append({
                        "email": email,
                        "email_type": "Valid",
                        "domain": validation["domain"],
                        "has_mx": True
                    })
                    valid_count += 1
                else:
                    results.append({
                        "email": email,
                        "email_type": "Risky",
                        "domain": validation["domain"],
                        "has_mx": False,
                        "reason": "No MX records"
                    })
                    risky_count += 1
            else:
                results.append({
                    "email": email,
                    "email_type": "Invalid",
                    "domain": "",
                    "has_mx": False,
                    "reason": validation["reason"]
                })
                invalid_count += 1
        
        return {
            "success": True,
            "results": results,
            "statistics": {
                "total": len(results),
                "valid": valid_count,
                "invalid": invalid_count,
                "risky": risky_count
            }
        }
        
    except Exception as e:
        logger.error(f"Validation error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

# ===================== SMTP TEST =====================
@app.post("/api/test-smtp")
async def test_smtp(
    host: str = Form(...),
    port: int = Form(587),
    username: str = Form(""),
    password: str = Form(""),
    use_tls: bool = Form(True),
    use_ssl: bool = Form(False)
):
    try:
        logger.info(f"Testing SMTP: {host}:{port}")
        
        password = password.replace(' ', '')
        
        if use_ssl:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, timeout=10, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
        
        if username and password:
            server.login(username, password)
            logger.info(f"✅ Logged in as {username}")
        
        server.quit()
        return {"success": True, "message": "SMTP connection successful"}
        
    except smtplib.SMTPAuthenticationError:
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Authentication failed. Use App Password."}
        )
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(e)}
        )

# ===================== SEND EMAILS =====================
@app.post("/api/send")
async def send_emails(request: dict):
    try:
        recipients = request.get("recipients", [])
        smtp_config = request.get("smtp_config", {})
        subject = request.get("subject", "No Subject")
        body = request.get("body", "")
        use_html = request.get("use_html", True)
        dry_run = request.get("dry_run", False)
        inline_images = request.get("inline_images", [])
        attachments = request.get("attachments", [])
        rate = request.get("rate", 0.5)
        
        logger.info(f"📧 Send: {len(recipients)} recipients, {len(inline_images)} inline, {len(attachments)} attachments")
        
        if dry_run:
            return {
                "success": True,
                "summary": {
                    "total": len(recipients),
                    "sent": len(recipients),
                    "failed": 0,
                    "dry_run": True
                }
            }
        
        if not smtp_config.get('smtp_host'):
            return JSONResponse(status_code=400, content={"success": False, "error": "SMTP host required"})
        
        smtp_host = smtp_config.get('smtp_host')
        smtp_port = smtp_config.get('smtp_port', 587)
        smtp_user = smtp_config.get('smtp_user', '').strip()
        smtp_pass = smtp_config.get('smtp_pass', '').replace(' ', '')
        from_addr = smtp_config.get('from_addr') or smtp_user
        
        sent = 0
        failed = 0
        
        for i, recipient in enumerate(recipients):
            try:
                # Create message
                msg = MIMEMultipart('mixed')
                msg['From'] = from_addr
                msg['To'] = recipient
                msg['Subject'] = subject
                msg['Date'] = formatdate(localtime=True)
                
                # Create related part for inline images
                related = MIMEMultipart('related')
                
                # Add HTML part
                if use_html:
                    html_part = MIMEText(body, 'html', 'utf-8')
                else:
                    html_part = MIMEText(body, 'plain', 'utf-8')
                related.attach(html_part)
                
                # Add inline images
                for idx, img in enumerate(inline_images):
                    try:
                        img_data = base64.b64decode(img['content'])
                        img_subtype = 'jpeg'
                        if img['filename'].lower().endswith('.png'):
                            img_subtype = 'png'
                        elif img['filename'].lower().endswith('.gif'):
                            img_subtype = 'gif'
                        
                        img_part = MIMEImage(img_data, _subtype=img_subtype)
                        img_part.add_header('Content-ID', f'<image{idx}>')
                        img_part.add_header('Content-Disposition', 'inline', filename=img['filename'])
                        related.attach(img_part)
                    except Exception as e:
                        logger.error(f"Error adding inline image: {e}")
                
                msg.attach(related)
                
                # Add file attachments
                for att in attachments:
                    try:
                        file_data = base64.b64decode(att['content'])
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(file_data)
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition', f'attachment; filename="{att["filename"]}"')
                        msg.attach(part)
                    except Exception as e:
                        logger.error(f"Error adding attachment: {e}")
                
                # Connect and send
                if smtp_config.get('use_ssl'):
                    server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
                else:
                    server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
                    server.ehlo()
                    if smtp_config.get('use_tls'):
                        server.starttls()
                        server.ehlo()
                
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
                server.quit()
                
                sent += 1
                logger.info(f"✅ Sent to {recipient}")
                
                if i < len(recipients) - 1 and rate > 0:
                    await asyncio.sleep(rate)
                    
            except Exception as e:
                logger.error(f"❌ Failed to send to {recipient}: {str(e)}")
                failed += 1
        
        return {
            "success": True,
            "summary": {
                "total": len(recipients),
                "sent": sent,
                "failed": failed
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Send error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("📧 EMAIL MARKETING BACKEND")
    print("=" * 60)
    print("✅ Server: http://localhost:8000")
    print("=" * 60)
    print("📌 GMAIL SETUP:")
    print("1. Enable 2FA: https://myaccount.google.com/security")
    print("2. Get App Password: https://myaccount.google.com/apppasswords")
    print("3. Use that 16-digit password")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)