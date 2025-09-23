#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import requests
import json
from datetime import datetime
import os

app = Flask(__name__)

# Configuration - UPDATE THESE VALUES
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', "xoxb-1226494846485-9553183493383-8n3xVaeXMYx3ZGFuduLH0yPu")
TRACKING_CHANNEL = os.environ.get('TRACKING_CHANNEL', "C09C1AAR8CB")
BASE_URL = "https://slack.com/api"

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
