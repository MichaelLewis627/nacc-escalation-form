#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import requests
import json
from datetime import datetime
import os
import boto3
import uuid

app = Flask(__name__)

# Configuration - Environment Variables
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', "xoxb-1226494846485-9553183493383-JzptkwpjdYKC4pucTcSphg39")
TRACKING_CHANNEL = os.environ.get('TRACKING_CHANNEL', "C09C1AAR8CB")
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'nacc-escalation-logs')
BASE_URL = "https://slack.com/api"

# Initialize S3 client
s3_client = None
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    s3_client = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name='us-east-1'
    )

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

def log_to_s3(submission_data):
    """Log submission data to S3 bucket"""
    if not s3_client:
        print("S3 client not configured, skipping S3 logging")
        return False
    
    try:
        # Create unique filename with timestamp
        timestamp = datetime.now().strftime('%Y/%m/%d')
        submission_id = str(uuid.uuid4())
        filename = f"escalations/{timestamp}/{submission_id}.json"
        
        # Add metadata
        log_data = {
            "submission_id": submission_id,
            "timestamp": datetime.now().isoformat(),
            "form_data": submission_data,
            "source": "nacc-escalation-form"
        }
        
        # Upload to S3
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=filename,
            Body=json.dumps(log_data, indent=2),
            ContentType='application/json'
        )
        
        print(f"Successfully logged to S3: {filename}")
        return True
        
    except Exception as e:
        print(f"Failed to log to S3: {str(e)}")
        return False

@app.route('/')
def index():
    return send_from_directory('.', 'nacc_escalation_form.html')

@app.route('/submit-escalation', methods=['POST'])
def submit_escalation():
    try:
        data = request.json
        
        # Log to S3 first
        log_to_s3(data)
        
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
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("🚀 Starting NACC Escalation Form Server...")
    print(f"🌐 Form will be available on port {port}")
    if s3_client:
        print(f"📊 S3 logging enabled to bucket: {S3_BUCKET_NAME}")
    else:
        print("⚠️  S3 logging disabled - add AWS credentials to enable")
    app.run(debug=False, host='0.0.0.0', port=port)
