"""
main.py - Main Streamlit Dashboard
Combined version with all features
"""
import streamlit as st
import pandas as pd
import time
import socket
import smtplib
from email.message import EmailMessage
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
import os
import sys
import base64
from datetime import datetime

# Import our modules
try:
    from validator_core import process_one, load_disposable_set
    from send_mail import test_smtp_connection, send_bulk_emails
except ImportError as e:
    st.error(f"Import error: {e}. Make sure validator_core.py and send_mail.py are in the same directory.")
    st.stop()

# Page config
st.set_page_config(
    page_title="Email Validator & Automation",
    page_icon="📧",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        color: #1f77b4;
        margin-bottom: 1rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #1f77b4;
    }
    .success { color: #28a745; }
    .error { color: #dc3545; }
    .warning { color: #ffc107; }
    .stProgress > div > div > div > div {
        background-color: #1f77b4;
    }
    .uploaded-file {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# Session state initialization
if 'validated_data' not in st.session_state:
    st.session_state.validated_data = None
if 'send_results' not in st.session_state:
    st.session_state.send_results = None
if 'attachments' not in st.session_state:
    st.session_state.attachments = []
if 'smtp_config' not in st.session_state:
    st.session_state.smtp_config = {
        'host': 'smtp.gmail.com',
        'port': 587,
        'username': '',
        'password': '',
        'from_addr': 'verify@example.com',
        'use_ssl': False,
        'use_tls': True
    }

def safe_merge(original_df, results_df, suffix="_validated"):
    """Merge dataframes safely avoiding column name conflicts"""
    conflicts = set(original_df.columns).intersection(results_df.columns)
    if conflicts:
        rename_map = {c: f"{c}{suffix}" for c in conflicts}
        results_df = results_df.rename(columns=rename_map)
    return pd.concat([original_df.reset_index(drop=True), 
                     results_df.reset_index(drop=True)], axis=1)

def process_attachments(uploaded_files):
    """Process uploaded files into attachment format"""
    attachments = []
    for uploaded_file in uploaded_files:
        try:
            # Read file content
            file_bytes = uploaded_file.read()
            
            # Convert to base64
            content_b64 = base64.b64encode(file_bytes).decode('utf-8')
            
            attachments.append({
                'filename': uploaded_file.name,
                'content': content_b64,
                'content_type': uploaded_file.type or 'application/octet-stream',
                'size': len(file_bytes)
            })
        except Exception as e:
            st.error(f"Error processing {uploaded_file.name}: {e}")
    
    return attachments

def main():
    # Header
    st.markdown('<h1 class="main-header">📧 Email Validator & Automation Dashboard</h1>', 
                unsafe_allow_html=True)
    st.markdown("---")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Settings")
        
        # Validation settings
        st.subheader("🔍 Validation Settings")
        do_smtp_check = st.checkbox("Enable SMTP Check", value=False, 
                                   help="Perform real SMTP verification (slower but more accurate)")
        validation_timeout = st.slider("Timeout (seconds)", 2, 30, 8)
        validation_workers = st.slider("Threads", 1, 20, 8)
        
        st.markdown("---")
        
        # SMTP Configuration
        st.subheader("📧 SMTP Configuration")
        smtp_host = st.text_input("SMTP Host", 
                                 value=st.session_state.smtp_config['host'])
        smtp_port = st.number_input("Port", value=587, min_value=1, max_value=65535)
        smtp_user = st.text_input("Username", 
                                 value=st.session_state.smtp_config['username'])
        smtp_pass = st.text_input("Password", type="password",
                                 value=st.session_state.smtp_config['password'])
        from_addr = st.text_input("From Address", 
                                 value=st.session_state.smtp_config['from_addr'])
        
        col1, col2 = st.columns(2)
        with col1:
            use_ssl = st.checkbox("Use SSL", value=st.session_state.smtp_config['use_ssl'])
        with col2:
            use_tls = st.checkbox("Use TLS", value=st.session_state.smtp_config['use_tls'])
        
        # Save SMTP config
        if st.button("💾 Save SMTP Config", use_container_width=True):
            st.session_state.smtp_config.update({
                'host': smtp_host,
                'port': smtp_port,
                'username': smtp_user,
                'password': smtp_pass,
                'from_addr': from_addr,
                'use_ssl': use_ssl,
                'use_tls': use_tls
            })
            st.success("✅ SMTP configuration saved!")
        
        # Test connection
        if st.button("🔌 Test SMTP Connection", use_container_width=True):
            with st.spinner("Testing connection..."):
                success, message = test_smtp_connection(
                    smtp_host, smtp_port, smtp_user, smtp_pass, use_tls, use_ssl
                )
                if success:
                    st.success(f"✅ {message}")
                else:
                    st.error(f"❌ {message}")
    
    # Main content - Tabs
    tab1, tab2, tab3, tab4 = st.tabs([
        "📤 Upload & Validate", 
        "✅ Results", 
        "✉️ Send Emails", 
        "📊 Reports"
    ])
    
    # Tab 1: Upload & Validate
    with tab1:
        st.header("📤 Upload & Validate Emails")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            uploaded_file = st.file_uploader(
                "Choose a file",
                type=["csv", "xlsx", "xls", "txt"],
                help="Upload CSV, Excel, or TXT file containing email addresses"
            )
        
        with col2:
            email_col = st.text_input("Email Column Name", value="email")
            st.info("For TXT files, use 'email' as column name")
        
        if uploaded_file:
            # Display file info
            st.markdown(f"""
            <div class="uploaded-file">
                <strong>📁 {uploaded_file.name}</strong><br>
                Size: {uploaded_file.size / 1024:.1f} KB
            </div>
            """, unsafe_allow_html=True)
            
            # Read file
            try:
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file, dtype=str)
                elif uploaded_file.name.endswith(('.xlsx', '.xls')):
                    df = pd.read_excel(uploaded_file, dtype=str)
                elif uploaded_file.name.endswith('.txt'):
                    # Read as text file with one email per line
                    content = uploaded_file.read().decode('utf-8')
                    emails = [line.strip() for line in content.split('\n') if line.strip()]
                    df = pd.DataFrame({'email': emails})
                else:
                    st.error(f"Unsupported file type: {uploaded_file.name}")
                    st.stop()
                
                st.success(f"✅ Loaded {len(df)} rows")
                
                if email_col not in df.columns:
                    st.error(f"❌ Column '{email_col}' not found. Available columns: {list(df.columns)}")
                    if len(df.columns) > 0:
                        st.info(f"Try using: {df.columns[0]}")
                else:
                    # Show preview
                    with st.expander("🔍 Preview Data", expanded=True):
                        st.dataframe(df.head(10), use_container_width=True)
                        
                        # Show email samples
                        sample_emails = df[email_col].dropna().head(5).tolist()
                        st.write("**Sample emails:**")
                        for email in sample_emails:
                            st.write(f"- {email}")
                    
                    # Validate button
                    if st.button("🚀 Start Validation", type="primary", use_container_width=True):
                        with st.spinner(f"Validating {len(df)} emails..."):
                            emails_list = df[email_col].fillna("").astype(str).tolist()
                            emails_list = [e.strip() for e in emails_list if e.strip()]
                            
                            if not emails_list:
                                st.error("No valid emails found in the file")
                                st.stop()
                            
                            disposable_set = load_disposable_set()
                            
                            results = [None] * len(emails_list)
                            
                            with ThreadPoolExecutor(max_workers=validation_workers) as executor:
                                futures = {
                                    executor.submit(
                                        process_one,
                                        email,
                                        disposable_set,
                                        do_smtp_check,
                                        validation_timeout,
                                        from_addr or "verify@example.com"
                                    ): idx
                                    for idx, email in enumerate(emails_list)
                                }
                                
                                progress_bar = st.progress(0)
                                status_text = st.empty()
                                
                                for i, future in enumerate(as_completed(futures)):
                                    idx = futures[future]
                                    try:
                                        results[idx] = future.result()
                                    except Exception as e:
                                        results[idx] = {
                                            "email": emails_list[idx],
                                            "email_type": "Error",
                                            "reason": str(e)[:100],
                                            "domain": "",
                                            "has_mx": False,
                                            "syntax_valid": False
                                        }
                                    progress = (i + 1) / len(futures)
                                    progress_bar.progress(progress)
                                    status_text.text(f"Processed {i + 1}/{len(futures)} emails")
                            
                            # Create results dataframe
                            results_df = pd.DataFrame(results)
                            final_df = safe_merge(df, results_df)
                            
                            # Store in session state
                            st.session_state.validated_data = {
                                'df': final_df,
                                'filename': uploaded_file.name,
                                'original_df': df,
                                'email_col': email_col,
                                'stats': {
                                    'total': len(results),
                                    'valid': len([r for r in results if r.get('email_type') == 'Valid']),
                                    'invalid': len([r for r in results if r.get('email_type') == 'Invalid']),
                                    'risky': len([r for r in results if r.get('email_type') == 'Risky']),
                                    'error': len([r for r in results if r.get('email_type') == 'Error'])
                                }
                            }
                            
                            st.success("✅ Validation complete!")
                            st.rerun()
            
            except Exception as e:
                st.error(f"Error processing file: {str(e)}")
                st.exception(e)
    
    # Tab 2: Results
    with tab2:
        st.header("✅ Validation Results")
        
        if st.session_state.validated_data:
            data = st.session_state.validated_data
            df = data['df']
            stats = data.get('stats', {})
            
            # Stats
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("✅ Valid", stats.get('valid', 0), 
                         help="Emails that passed all checks")
            with col2:
                st.metric("❌ Invalid", stats.get('invalid', 0),
                         help="Emails that failed validation")
            with col3:
                st.metric("⚠️ Risky", stats.get('risky', 0),
                         help="Emails that are disposable or role-based")
            with col4:
                st.metric("📊 Total", stats.get('total', 0))
            
            # Filters
            st.subheader("🔍 Filters")
            filter_col1, filter_col2, filter_col3 = st.columns(3)
            with filter_col1:
                show_valid = st.checkbox("Show Valid", value=True)
            with filter_col2:
                show_invalid = st.checkbox("Show Invalid", value=False)
            with filter_col3:
                show_risky = st.checkbox("Show Risky", value=True)
            
            # Apply filters
            filtered_df = df.copy()
            if 'email_type' in filtered_df.columns:
                conditions = []
                if show_valid:
                    conditions.append(filtered_df['email_type'] == 'Valid')
                if show_invalid:
                    conditions.append(filtered_df['email_type'] == 'Invalid')
                if show_risky:
                    conditions.append(filtered_df['email_type'] == 'Risky')
                
                if conditions:
                    mask = pd.concat(conditions, axis=1).any(axis=1)
                    filtered_df = filtered_df[mask]
            
            # Display table
            st.dataframe(filtered_df, use_container_width=True, height=400)
            
            # Download button
            csv = filtered_df.to_csv(index=False)
            st.download_button(
                label="📥 Download Results (CSV)",
                data=csv,
                file_name=f"validated_{data['filename']}",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("📭 No validation results yet. Upload and validate a file first.")
    
    # Tab 3: Send Emails
    with tab3:
        st.header("✉️ Send Automated Replies")
        
        if not st.session_state.validated_data:
            st.warning("⚠️ Please validate emails first in the Upload & Validate tab.")
        else:
            data = st.session_state.validated_data
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("📝 Email Content")
                subject = st.text_input("Subject", value=f"Re: Your inquiry - {datetime.now().strftime('%Y-%m-%d')}")
                
                # Email body templates
                body_template = st.selectbox(
                    "Template",
                    ["Custom", "Business Reply", "Thank You", "Information"]
                )
                
                default_body = {
                    "Custom": """Hello,

This is an automated reply to your message.

Best regards,
The Team""",
                    "Business Reply": """Dear Sir/Madam,

Thank you for your inquiry. We have received your message and will get back to you shortly.

Best regards,
Business Team""",
                    "Thank You": """Dear Valued Customer,

Thank you for contacting us. We appreciate your interest in our services.

Best regards,
Customer Service""",
                    "Information": """Hello,

Thank you for your interest. Please find the information you requested below.

Best regards,
Information Desk"""
                }
                
                body = st.text_area("Body", height=200, value=default_body[body_template])
                
                use_html = st.checkbox("Use HTML Formatting", value=False,
                                     help="Enable HTML formatting in email")
            
            with col2:
                st.subheader("📎 Attachments")
                attachment_files = st.file_uploader(
                    "Choose files to attach",
                    type=['pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png', 'gif', 'xlsx', 'xls', 'csv'],
                    accept_multiple_files=True,
                    help="You can select multiple files"
                )
                
                if attachment_files:
                    if st.button("📎 Process Attachments"):
                        with st.spinner("Processing attachments..."):
                            st.session_state.attachments = process_attachments(attachment_files)
                            st.success(f"✅ {len(st.session_state.attachments)} attachments ready")
                
                # Show current attachments
                if st.session_state.attachments:
                    st.write("**Current Attachments:**")
                    for att in st.session_state.attachments:
                        col_a, col_b = st.columns([3, 1])
                        with col_a:
                            st.write(f"📄 {att['filename']}")
                        with col_b:
                            size_kb = att['size'] / 1024
                            st.write(f"{size_kb:.1f} KB")
                    
                    if st.button("🗑️ Clear Attachments"):
                        st.session_state.attachments = []
                        st.rerun()
            
            st.markdown("---")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                only_valid = st.checkbox("Send only to Valid emails", value=True,
                                       help="Only send to emails marked as Valid")
            with col2:
                dry_run = st.checkbox("Dry Run (Test without sending)", value=True,
                                    help="Simulate sending without actually sending emails")
            with col3:
                send_rate = st.number_input("Rate limit (seconds)", 0.0, 10.0, 0.5, 0.1,
                                          help="Delay between emails")
            
            # Get recipients
            if only_valid:
                recipients_df = data['df'][data['df']['email_type'] == 'Valid']
            else:
                recipients_df = data['df'][data['df']['email_type'].isin(['Valid', 'Risky'])]
            
            recipients = recipients_df[data['email_col']].dropna().unique().tolist()
            
            st.info(f"📧 **{len(recipients)}** recipients selected")
            
            if st.button("🚀 Send Emails", type="primary", use_container_width=True):
                if not recipients:
                    st.warning("No recipients to send to.")
                elif not dry_run and not st.session_state.smtp_config['host']:
                    st.warning("Please configure SMTP settings in the sidebar.")
                else:
                    # Send emails
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    with st.spinner("Sending emails..."):
                        results = send_bulk_emails(
                            recipients=recipients,
                            smtp_config=st.session_state.smtp_config,
                            subject=subject,
                            body=body,
                            attachments=st.session_state.attachments if not dry_run else None,
                            use_html=use_html,
                            dry_run=dry_run,
                            workers=validation_workers,
                            rate_limit=send_rate,
                            max_retries=3
                        )
                    
                    # Store results
                    st.session_state.send_results = pd.DataFrame(results)
                    
                    # Show summary
                    st.success("✅ Send operation completed!")
                    
                    # Results summary
                    if 'status' in st.session_state.send_results.columns:
                        sent = len(st.session_state.send_results[st.session_state.send_results['status'] == 'sent'])
                        failed = len(st.session_state.send_results[st.session_state.send_results['status'] == 'failed'])
                        dry = len(st.session_state.send_results[st.session_state.send_results['status'] == 'dry-run'])
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("✅ Sent", sent)
                        with col2:
                            st.metric("❌ Failed", failed)
                        with col3:
                            st.metric("📝 Dry Run", dry)
                    
                    # Display results
                    if st.session_state.send_results is not None:
                        st.dataframe(st.session_state.send_results, use_container_width=True)
                        
                        # Download results
                        csv = st.session_state.send_results.to_csv(index=False)
                        st.download_button(
                            label="📥 Download Send Results",
                            data=csv,
                            file_name=f"send_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                            mime="text/csv",
                            use_container_width=True
                        )
    
    # Tab 4: Reports
    with tab4:
        st.header("📊 Reports & Analytics")
        
        if st.session_state.validated_data:
            df = st.session_state.validated_data['df']
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("📊 Validation Statistics")
                if 'email_type' in df.columns:
                    chart_data = df['email_type'].value_counts()
                    st.bar_chart(chart_data)
                    
                    # Pie chart alternative
                    st.write("**Breakdown:**")
                    for status, count in chart_data.items():
                        percentage = (count / len(df)) * 100
                        st.write(f"- {status}: {count} ({percentage:.1f}%)")
            
            with col2:
                st.subheader("🌐 Domain Analysis")
                if 'domain' in df.columns:
                    # Filter out empty domains
                    domains = df[df['domain'].notna() & (df['domain'] != '')]['domain']
                    if len(domains) > 0:
                        top_domains = domains.value_counts().head(10)
                        st.write("**Top 10 Domains:**")
                        st.dataframe(top_domains, use_container_width=True)
            
            # MX Records Analysis
            st.subheader("📡 MX Records Analysis")
            if 'has_mx' in df.columns:
                mx_counts = df['has_mx'].value_counts()
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("✅ Has MX Records", mx_counts.get(True, 0))
                with col2:
                    st.metric("❌ No MX Records", mx_counts.get(False, 0))
            
            # Risky emails analysis
            st.subheader("⚠️ Risky Emails Analysis")
            if 'is_disposable' in df.columns and 'is_role_based' in df.columns:
                disposable = df[df['is_disposable'] == True].shape[0]
                role_based = df[df['is_role_based'] == True].shape[0]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("📧 Disposable Domains", disposable)
                with col2:
                    st.metric("👥 Role-based Emails", role_based)
            
            # SMTP Status
            if 'smtp_status' in df.columns:
                st.subheader("📨 SMTP Status")
                smtp_counts = df['smtp_status'].dropna().value_counts().head(5)
                st.dataframe(smtp_counts, use_container_width=True)
        else:
            st.info("📭 No data available for reports. Please validate emails first.")

if __name__ == "__main__":
    main()