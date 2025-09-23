#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import requests
import json
from datetime import datetime
import os
import re
import boto3
import csv
from io import StringIO

app = Flask(__name__)

# Configuration - UPDATE THESE VALUES
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', "xoxb-1226494846485-9553183493383-8n3xVaeXMYx3ZGFuduLH0yPu")
TRACKING_CHANNEL = os.environ.get('TRACKING_CHANNEL', "C09C1AAR8CB")
BASE_URL = "https://slack.com/api"
S3_BUCKET = os.environ.get('S3_BUCKET', 'nacc-escalation-data')
AWS_ACCESS_KEY = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')

def send_slack_message(channel, text, user_id=None):
    """Send message to Slack channel or user"""
    url = f"{BASE_URL}/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "channel": channel if channel.startswith('#') or channel.startswith('C') else f"@{channel}",
        "text": text,
        "unfurl_links": False
    }
    
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

def get_sim_ticket_severity(sim_link):
    """Extract severity from SIM ticket"""
    if not sim_link:
        return None
    
    # Extract ticket ID from various SIM URL formats
    patterns = [
        r't\.corp\.amazon\.com/([VPT]\d+)',
        r'issues\.amazon\.com/issues/([A-Z]+-\d+)',
        r'taskei\.amazon\.dev/tasks/([A-Z]+-\d+)',
        r'i\.amazon\.com/([A-Z]+-\d+)',
        r'sim\.amazon\.com/issues/([A-Z]+-\d+)',
        r'([VPT]\d+)',
        r'([A-Z]+-\d+)'
    ]
    
    ticket_id = None
    for pattern in patterns:
        match = re.search(pattern, sim_link)
        if match:
            ticket_id = match.group(1)
            break
    
    if not ticket_id:
        return None
    
    # Try t.corp.amazon.com API for V/P/T tickets
    try:
        response = requests.get(f"https://t.corp.amazon.com/api/tickets/{ticket_id}")
        if response.status_code == 200:
            data = response.json()
            severity = data.get('extensions', {}).get('tt', {}).get('impact')
            if severity:
                return str(severity)
    except:
        pass
    
    # Try taskei API
    try:
        response = requests.get(f"https://taskei.amazon.dev/api/tasks/{ticket_id}")
        if response.status_code == 200:
            data = response.json()
            priority = data.get('priority', '').lower()
            if 'high' in priority:
                return '2'
            elif 'medium' in priority:
                return '3'
            elif 'low' in priority:
                return '4'
    except:
        pass
    
    # Try issues.amazon.com API
    try:
        response = requests.get(f"https://issues.amazon.com/api/issues/{ticket_id}")
        if response.status_code == 200:
            data = response.json()
            severity = data.get('extensions', {}).get('tt', {}).get('impact')
            if severity:
                return str(severity)
    except:
        pass
    
    return None

def post_severity_mismatch_alert(claimed_sev, actual_sev, sim_ticket, submitter_alias):
    """Post bot alert for severity mismatch"""
    message = f"⚠️ **Severity Mismatch Detected**\n" \
              f"• Claimed: SEV-{claimed_sev}\n" \
              f"• Actual: SEV-{actual_sev}\n" \
              f"• SIM Ticket: {sim_ticket}\n" \
              f"• Submitter: {submitter_alias}"
    
    send_slack_message(TRACKING_CHANNEL, message)

def log_to_s3(data):
    """Log escalation data to S3"""
    try:
        s3 = boto3.client('s3', 
                         aws_access_key_id=AWS_ACCESS_KEY,
                         aws_secret_access_key=AWS_SECRET_KEY)
        
        # Create CSV row
        csv_data = StringIO()
        writer = csv.writer(csv_data)
        writer.writerow([
            data['timestamp'],
            data['claimed_sev'],
            data['actual_sev'],
            data['sim_ticket_sev'],
            data['false_escalation'],
            data['mismatch_reason']
        ])
        
        # Upload to S3
        key = f"escalations/{datetime.now().strftime('%Y/%m/%d')}/escalation_{int(datetime.now().timestamp())}.csv"
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=csv_data.getvalue())
        
    except Exception as e:
        print(f"S3 logging error: {e}")

def lookup_user_id(username):
    """Convert username to Slack user ID"""
    if username.startswith('@'):
        username = username[1:]
    
    url = f"{BASE_URL}/users.lookupByEmail"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    
    # Try email lookup first
    response = requests.get(url, headers=headers, params={"email": f"{username}@amazon.com"})
    if response.json().get("ok"):
        return response.json()["user"]["id"]
    
    # Fallback to username search
    url = f"{BASE_URL}/users.list"
    response = requests.get(url, headers=headers)
    if response.json().get("ok"):
        for user in response.json()["members"]:
            if user.get("name") == username or user.get("real_name", "").lower() == username.lower():
                return user["id"]
    
    return username  # Return original if not found

@app.route('/')
def index():
    return send_from_directory('.', 'nacc_escalation_form.html')

@app.route('/submit-escalation', methods=['POST'])
def submit_escalation():
    try:
        data = request.json
        
        # Extract claimed severity and validate against SIM ticket
        claimed_severity = data.get('severity', '3')
        sim_link = data.get('simLink', '')
        actual_severity = get_sim_ticket_severity(sim_link) if sim_link else None
        
        # Check for severity mismatch
        is_false_escalation = False
        mismatch_reason = ""
        
        if actual_severity and claimed_severity != actual_severity:
            is_false_escalation = True
            mismatch_reason = f"Claimed SEV-{claimed_severity} but ticket is SEV-{actual_severity}"
            
            # Post severity mismatch alert
            post_severity_mismatch_alert(
                claimed_severity, 
                actual_severity, 
                sim_link, 
                data.get('submitterAlias', 'Unknown')
            )
        
        # Log to S3
        log_data = {
            'timestamp': datetime.now().isoformat(),
            'claimed_sev': claimed_severity,
            'actual_sev': actual_severity or 'Unknown',
            'sim_ticket_sev': actual_severity or 'N/A',
            'false_escalation': is_false_escalation,
            'mismatch_reason': mismatch_reason
        }
        log_to_s3(log_data)
        
        # Format tracking message
        tracking_message = f"""🚨 NACC PO Escalation Request

Type: {data['escalationType']}
Station: {data['station']}
Coupa Link: {data['coupaLink']}
SIM Link: {data.get('simLink', 'Not provided')}
Need by: {data['needByDate']}
Description: {data['description']}
First Approver: {data['firstApprover']}
Second Approver: {data['secondApprover']}

Submitted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        # Send to tracking channel
        send_slack_message(TRACKING_CHANNEL, tracking_message)
        
        # Send to approvers
        approver_msg = "Hello, you have a new Coupa escalation in the nacc-escalations-tracking Slack Channel. Please action it accordingly."
        send_slack_message(data['firstApprover'], approver_msg)
        send_slack_message(data['secondApprover'], approver_msg)
        
        return jsonify({"success": True})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("🚀 Starting NACC Escalation Form Server...")
    print(f"🌐 Form will be available on port {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
