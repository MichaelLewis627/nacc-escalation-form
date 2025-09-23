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
    """Log submission data to S3 as CSV"""
    if not s3_client:
        print("S3 client not configured, skipping S3 logging")
        return False
    
    try:
        csv_filename = "escalations/nacc_escalations.csv"
        
        # Prepare CSV row
        csv_row = [
            datetime.now().isoformat(),
            str(uuid.uuid4()),
            submission_data.get('escalationType', ''),
            submission_data.get('station', ''),
            submission_data.get('coupaLink', ''),
            submission_data.get('simLink', ''),
            submission_data.get('needByDate', ''),
            submission_data.get('description', '').replace(',', ';').replace('\n', ' '),
            submission_data.get('firstApprover', ''),
            submission_data.get('secondApprover', ''),
            'nacc-escalation-form'
        ]
        
        # Check if file exists
        file_exists = False
        try:
            s3_client.head_object(Bucket=S3_BUCKET_NAME, Key=csv_filename)
            file_exists = True
        except:
            file_exists = False
        
        # Get existing content if file exists
        existing_content = ""
        if file_exists:
            try:
                response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=csv_filename)
                existing_content = response['Body'].read().decode('utf-8')
            except:
                existing_content = ""
        
        # Create CSV content
        if not file_exists or not existing_content:
            # Create header if new file
            csv_content = "timestamp,submission_id,escalation_type,station,coupa_link,sim_link,need_by_date,description,first_approver,second_approver,source\n"
        else:
            csv_content = existing_content
        
        # Add new row
        csv_content += ",".join([f'"{field}"' for field in csv_row]) + "\n"
        
        # Upload updated CSV
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=csv_filename,
            Body=csv_content,
            ContentType='text/csv'
        )
        
        print(f"Successfully logged to S3 CSV: {csv_filename}")
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
        
        # Log to S3 CSV first
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
        print(f"📊 S3 CSV logging enabled to: {S3_BUCKET_NAME}/escalations/nacc_escalations.csv")
    else:
        print("⚠️  S3 logging disabled - add AWS credentials to enable")
    app.run(debug=False, host='0.0.0.0', port=port)
