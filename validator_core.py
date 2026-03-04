"""
validator_core.py - Core email validation engine
"""
import re
import dns.resolver
import smtplib
import socket
from typing import Tuple, List, Dict, Optional, Set

# Common disposable domains
DISPOSABLE_DOMAINS = {
    'tempmail.com', 'throwaway.com', 'mailinator.com', 'guerrillamail.com',
    'yopmail.com', '10minutemail.com', 'temp-mail.org', 'fakeinbox.com',
    'maildrop.cc', 'getnada.com', 'trashmail.com', 'dispostable.com',
    'spamgourmet.com', 'throwawaymail.com', 'tempail.com', 'eyepaste.com',
    'sharklasers.com', 'guerrillamail.org', 'grr.la', 'mailinator.net',
    'mailinator2.com', 'mailinator3.com', 'mailinator4.com'
}

# Common role-based emails
ROLE_BASED_PREFIXES = {
    'admin', 'support', 'info', 'sales', 'contact', 'webmaster',
    'postmaster', 'hostmaster', 'abuse', 'noreply', 'no-reply',
    'newsletter', 'office', 'help', 'service', 'marketing',
    'billing', 'accounts', 'careers', 'jobs', 'hr', 'team',
    'hello', 'enquiries', 'inquiries', 'feedback'
}

def validate_syntax(email: str) -> Tuple[bool, str, str, str]:
    """Validate email syntax and extract components"""
    if not email or not isinstance(email, str):
        return False, "", "", "Empty email"
    
    email = email.strip().lower()
    
    # Basic regex pattern
    pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*[a-zA-Z0-9]@[a-zA-Z0-9][a-zA-zA-Z0-9.-]*\.[a-zA-Z]{2,}$'
    
    if not re.match(pattern, email):
        return False, "", "", "Invalid email format"
    
    # Extract parts
    try:
        local, domain = email.rsplit('@', 1)
    except ValueError:
        return False, "", "", "No @ symbol"
    
    # Length checks
    if len(email) > 254:
        return False, local, domain, "Email too long (>254 chars)"
    
    if len(local) > 64:
        return False, local, domain, "Local part too long (>64 chars)"
    
    if len(domain) > 255:
        return False, local, domain, "Domain too long (>255 chars)"
    
    # Local part validation
    if local.startswith('.') or local.endswith('.'):
        return False, local, domain, "Local part cannot start/end with dot"
    
    if '..' in local:
        return False, local, domain, "Consecutive dots not allowed"
    
    # Domain validation
    if domain.startswith('.') or domain.endswith('.'):
        return False, local, domain, "Domain cannot start/end with dot"
    
    if '..' in domain:
        return False, local, domain, "Consecutive dots in domain"
    
    return True, local, domain, "Valid syntax"

def check_disposable(domain: str) -> bool:
    """Check if domain is disposable"""
    return domain in DISPOSABLE_DOMAINS

def check_role_based(local: str) -> bool:
    """Check if email is role-based"""
    return local in ROLE_BASED_PREFIXES

def check_mx_record(domain: str, timeout: int = 5) -> Tuple[bool, List[str]]:
    """Check MX records for domain"""
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        
        try:
            mx_records = resolver.resolve(domain, 'MX')
            mx_hosts = [str(r.exchange).rstrip('.') for r in mx_records]
            return bool(mx_hosts), mx_hosts
        except dns.resolver.NoAnswer:
            # Try A record as fallback
            try:
                a_records = resolver.resolve(domain, 'A')
                return True, [str(r) for r in a_records]
            except:
                return False, []
        except dns.resolver.NXDOMAIN:
            return False, []
        except Exception as e:
            return False, []
    except Exception as e:
        return False, []

def verify_smtp(email: str, from_addr: str = "verify@example.com", 
               timeout: int = 10) -> Tuple[bool, str]:
    """SMTP verification without sending email"""
    try:
        local, domain = email.split('@', 1)
        
        # Get MX records
        mx_found, mx_hosts = check_mx_record(domain, timeout)
        if not mx_found or not mx_hosts:
            return False, "No MX records found"
        
        # Try primary MX server
        mx = str(mx_hosts[0])
        
        # SMTP conversation
        try:
            # Connect to SMTP server
            smtp = smtplib.SMTP(timeout=timeout)
            smtp.connect(mx, 25)
            smtp.ehlo_or_helo_if_needed()
            
            # Check if recipient exists
            code, message = smtp.mail(from_addr)
            if code != 250:
                smtp.quit()
                return False, f"MAIL FROM rejected: {code}"
            
            code, message = smtp.rcpt(email)
            smtp.quit()
            
            if code == 250:
                return True, "accepted"
            elif code == 550:
                return False, "rejected (user unknown)"
            elif code == 450 or code == 451:
                return False, "temporarily unavailable"
            else:
                return False, f"rejected ({code})"
                
        except smtplib.SMTPServerDisconnected:
            return False, "connection closed"
        except smtplib.SMTPConnectError:
            return False, "connection failed"
        except socket.timeout:
            return False, "connection timeout"
        except Exception as e:
            return False, f"smtp error: {str(e)[:50]}"
            
    except Exception as e:
        return False, f"verification error: {str(e)[:50]}"

def process_one(email: str, disposable_set: Optional[Set] = None, 
               do_smtp: bool = False, timeout: int = 8, 
               from_addr: str = "verify@example.com") -> Dict:
    """Process single email - worker function"""
    # Syntax validation
    syntax_valid, local, domain, syntax_msg = validate_syntax(email)
    
    if not syntax_valid:
        return {
            "email": email,
            "email_type": "Invalid",
            "reason": syntax_msg,
            "syntax_valid": False,
            "domain": domain,
            "has_mx": False,
            "is_disposable": False,
            "is_role_based": False,
            "smtp_status": None,
            "mx_records": []
        }
    
    # MX record check
    has_mx, mx_records = check_mx_record(domain, timeout)
    
    # Additional checks
    is_disposable = check_disposable(domain)
    is_role_based = check_role_based(local)
    
    result = {
        "email": email,
        "email_type": "Valid",
        "reason": "Email is valid",
        "syntax_valid": True,
        "domain": domain,
        "has_mx": has_mx,
        "mx_records": mx_records[:3],
        "is_disposable": is_disposable,
        "is_role_based": is_role_based,
        "smtp_status": None
    }
    
    # SMTP verification (optional)
    if do_smtp and has_mx:
        smtp_valid, smtp_msg = verify_smtp(email, from_addr, timeout=timeout)
        result['smtp_status'] = smtp_msg
        
        if smtp_valid:
            result['email_type'] = 'Valid'
            result['reason'] = 'SMTP verification passed'
        else:
            if 'user unknown' in smtp_msg:
                result['email_type'] = 'Invalid'
                result['reason'] = f'SMTP: {smtp_msg}'
            else:
                result['email_type'] = 'Risky'
                result['reason'] = f'SMTP: {smtp_msg}'
    
    # Override for disposable/role-based
    if is_disposable:
        result['email_type'] = 'Risky'
        result['reason'] = 'Disposable email domain'
    
    if is_role_based:
        result['email_type'] = 'Risky'
        result['reason'] = 'Role-based email'
    
    return result

def load_disposable_set(file_path: Optional[str] = None) -> Set:
    """Load disposable domains from file"""
    if not file_path:
        return DISPOSABLE_DOMAINS.copy()
    
    try:
        with open(file_path, 'r') as f:
            domains = {line.strip().lower() for line in f if line.strip()}
        return domains.union(DISPOSABLE_DOMAINS)
    except:
        return DISPOSABLE_DOMAINS.copy()