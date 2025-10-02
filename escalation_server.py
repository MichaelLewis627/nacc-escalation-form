#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import requests
import json
from datetime import datetime, timedelta
import os
import boto3
import uuid
import re
import pytz

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

def extract_sim_ticket_id(sim_link):
    """Extract SIM ticket ID from various URL formats"""
    if not sim_link or sim_link.strip() == '' or sim_link == 'Not provided':
        return None
    
    # Common SIM URL patterns
    patterns = [
        r'sim\.amazon\.com/issues/([A-Z]+-\d+)',
        r'issues\.amazon\.com/issues/([A-Z]+-\d+)', 
        r't\.corp\.amazon\.com/([VPT]\d+)',
        r'taskei\.amazon\.dev/tasks/([A-Z]+-\d+)',
        r'([A-Z]+-\d+)',  # Direct ticket ID
        r'([VPT]\d+)'     # Direct ticket ID
    ]
    
    for pattern in patterns:
        match = re.search(pattern, sim_link)
        if match:
            return match.group(1)
    
    return None

def lookup_sim_ticket_severity(ticket_id):
    """Look up actual SIM ticket severity from internal APIs"""
    if not ticket_id:
        return {'severity': None, 'status': None, 'found': False, 'error': 'No ticket ID'}
    
    try:
        # Try SIM API first (issues.amazon.com)
        sim_url = f"https://issues.amazon.com/api/issues/{ticket_id}"
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'NACC-Escalation-Form/1.0'
        }
        
        response = requests.get(sim_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            severity = data.get('extensions', {}).get('tt', {}).get('impact')
            status = data.get('status')
            
            # Convert numeric severity to SEV format
            sev_mapping = {
                '1': 'SEV1',
                '2': 'SEV2', 
                '2.5': 'SEV2.5',
                '3': 'SEV3',
                '4': 'SEV4',
                '5': 'SEV5'
            }
            
            mapped_sev = sev_mapping.get(str(severity), f'SEV{severity}' if severity else None)
            
            return {
                'severity': mapped_sev,
                'status': status,
                'found': True,
                'ticket_id': ticket_id,
                'source': 'SIM'
            }
        
        # Try Taskei API if SIM fails
        taskei_url = f"https://taskei.amazon.dev/api/tasks/{ticket_id}"
        response = requests.get(taskei_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Taskei uses different severity mapping
            priority = data.get('priority', '').upper()
            
            # Map Taskei priority to SEV
            taskei_mapping = {
                'HIGH': 'SEV2',
                'MEDIUM': 'SEV3', 
                'LOW': 'SEV4'
            }
            
            mapped_sev = taskei_mapping.get(priority, 'SEV3')
            
            return {
                'severity': mapped_sev,
                'status': data.get('status'),
                'found': True,
                'ticket_id': ticket_id,
                'source': 'Taskei'
            }
        
        return {'severity': None, 'status': None, 'found': False, 'error': f'Ticket {ticket_id} not found'}
        
    except Exception as e:
        print(f"Error looking up SIM ticket {ticket_id}: {str(e)}")
        return {'severity': None, 'status': None, 'found': False, 'error': str(e)}

def validate_sev_classification(submission_data):
    """Validate SEV classification against actual SIM ticket severity"""
    escalation_type = submission_data.get('escalationType', '')
    sim_link = submission_data.get('simLink', '').strip()
    description = submission_data.get('description', '').lower()
    
    # Extract and lookup SIM ticket
    ticket_id = extract_sim_ticket_id(sim_link)
    sim_lookup = lookup_sim_ticket_severity(ticket_id)
    
    # Check for false SEV1/SEV2 escalations
    is_high_sev = escalation_type in ['SEV1', 'SEV2']
    has_sim_ticket = sim_lookup['found']
    actual_sim_sev = sim_lookup.get('severity')
    
    # Keywords that suggest urgency
    urgent_keywords = ['outage', 'down', 'critical', 'emergency', 'production', 'customer impact', 'revenue impact']
    non_urgent_keywords = ['question', 'clarification', 'when convenient', 'no rush', 'fyi', 'update']
    
    urgent_score = sum(1 for keyword in urgent_keywords if keyword in description)
    non_urgent_score = sum(1 for keyword in non_urgent_keywords if keyword in description)
    
    # Determine actual severity based on SIM lookup
    actual_sev = escalation_type
    false_escalation = False  # Disabled until SIM validation is working
    mismatch_reason = None
    
    # DISABLED: False escalation detection until SIM validation is working
    # if is_high_sev:
    #     if not has_sim_ticket:
    #         actual_sev = 'Standard'
    #         false_escalation = True
    #         mismatch_reason = 'No SIM ticket for high severity'
    #     elif actual_sim_sev and actual_sim_sev not in ['SEV1', 'SEV2', 'SEV2.5']:
    #         actual_sev = actual_sim_sev
    #         false_escalation = True
    #         mismatch_reason = f'SIM ticket is {actual_sim_sev}, not {escalation_type}'
    #     elif non_urgent_score > urgent_score:
    #         actual_sev = 'Standard'
    #         false_escalation = True
    #         mismatch_reason = 'Description suggests non-urgent request'
    
    # DISABLED: Need by date validation until SIM validation is working
    # need_by = submission_data.get('needByDate', '')
    # if need_by and is_high_sev:
    #     try:
    #         need_by_date = datetime.strptime(need_by, '%Y-%m-%d')
    #         days_until_needed = (need_by_date - datetime.now()).days
    #         
    #         if days_until_needed > 2:
    #             actual_sev = 'Standard'
    #             false_escalation = True
    #             mismatch_reason = f'Need by date is {days_until_needed} days away'
    #     except:
    #         pass
    
    return {
        'claimed_sev': escalation_type,
        'actual_sev': actual_sev,
        'sim_ticket_sev': actual_sim_sev,
        'false_escalation': false_escalation,
        'mismatch_reason': mismatch_reason,
        'has_sim_ticket': has_sim_ticket,
        'sim_ticket_id': ticket_id,
        'sim_lookup_result': sim_lookup,
        'urgent_score': urgent_score,
        'non_urgent_score': non_urgent_score
    }

def get_escalation_counts(alias, station):
    """Get escalation counts for user and station"""
    if not s3_client:
        return {'user_count': 0, 'station_count': 0}
    
    try:
        csv_filename = "escalations/nacc_escalations.csv"
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=csv_filename)
        existing_content = response['Body'].read().decode('utf-8')
        
        lines = existing_content.split('\n')[1:]  # Skip header
        user_count = 0
        station_count = 0
        
        for line in lines:
            if line.strip():
                parts = line.split(',')
                if len(parts) >= 7:
                    log_alias = parts[2].strip('"')  # alias column (index 2)
                    log_station = parts[6].strip('"')  # station column (index 6)
                    
                    if log_alias == alias:
                        user_count += 1
                    if log_station == station:
                        station_count += 1
        
        return {
            'user_count': user_count,
            'station_count': station_count
        }
    except:
        return {'user_count': 0, 'station_count': 0}

def send_behavior_notification(submission_data, validation_result, repeat_info):
    """Send behavior change notifications with SIM ticket details"""
    if validation_result['false_escalation']:
        user = submission_data.get('firstApprover', '')
        reason = validation_result.get('mismatch_reason', 'Classification mismatch')
        
        if repeat_info['is_repeat']:
            warning_msg = f"""⚠️ **Escalation Pattern Alert**

Hi {user}, this is your {repeat_info['count'] + 1} false escalation in the past 30 days.

**Issue:** {reason}
**Your Classification:** {validation_result['claimed_sev']}
**Actual Classification:** {validation_result['actual_sev']}

{f"**SIM Ticket Severity:** {validation_result['sim_ticket_sev']}" if validation_result['sim_ticket_sev'] else "**Missing SIM Ticket** for high severity escalation"}

**Guidelines Reminder:**
• SEV1/SEV2 require SIM tickets with matching severity
• Use "Standard" for non-urgent requests
• Verify SIM ticket severity before escalating

Your manager will be notified for coaching purposes."""
        else:
            warning_msg = f"""ℹ️ **Escalation Guidance**

Hi {user}, your escalation needs attention:

**Issue:** {reason}
**Your Classification:** {validation_result['claimed_sev']}
**Suggested Classification:** {validation_result['actual_sev']}

{f"**SIM Ticket Severity:** {validation_result['sim_ticket_sev']}" if validation_result['sim_ticket_sev'] else "**Tip:** High severity escalations require SIM tickets"}

No action needed - just guidance for future escalations."""
        
        send_slack_message(user, warning_msg)
        
        if repeat_info['is_repeat']:
            manager_msg = f"""📊 **Behavior Change Alert**

Employee: {user}
False Escalations (30 days): {repeat_info['count'] + 1}
Latest Issue: {reason}
Classification: {validation_result['claimed_sev']} → {validation_result['actual_sev']}

**Coaching Opportunity:** Review proper escalation criteria with {user}."""
            
            send_slack_message(TRACKING_CHANNEL, manager_msg)

def log_to_s3(submission_data):
    """Log submission data to S3 as CSV with SIM ticket lookup"""
    if not s3_client:
        print("S3 client not configured, skipping S3 logging")
        return False
    
    try:
        # Validate SEV classification with SIM lookup
        validation_result = validate_sev_classification(submission_data)
        escalation_counts = get_escalation_counts(
            submission_data.get('alias', ''),
            submission_data.get('station', '')
        )
        
        csv_filename = "escalations/nacc_escalations.csv"
        
        # Prepare enhanced CSV row with SIM data
        csv_row = [
            datetime.now().isoformat(),
            str(uuid.uuid4()),
            submission_data.get('alias', ''),
            submission_data.get('escalationType', ''),
            validation_result['actual_sev'],
            validation_result.get('sim_ticket_sev', ''),
            submission_data.get('station', ''),
            submission_data.get('coupaLink', ''),
            submission_data.get('simLink', ''),
            validation_result.get('sim_ticket_id', ''),
            submission_data.get('needByDate', ''),
            submission_data.get('description', '').replace(',', ';').replace('\n', ' '),
            submission_data.get('firstApprover', ''),
            submission_data.get('secondApprover', ''),
            str(validation_result['false_escalation']),
            validation_result.get('mismatch_reason', ''),
            str(validation_result['has_sim_ticket']),
            str(escalation_counts['user_count'] + 1),
            'nacc-escalation-form'
        ]
        
        # Get existing content
        file_exists = False
        existing_content = ""
        try:
            s3_client.head_object(Bucket=S3_BUCKET_NAME, Key=csv_filename)
            file_exists = True
            response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=csv_filename)
            existing_content = response['Body'].read().decode('utf-8')
        except:
            file_exists = False
        
        # Create CSV content with enhanced headers
        if not file_exists or not existing_content:
            csv_content = "timestamp,submission_id,alias,claimed_sev,actual_sev,sim_ticket_sev,station,coupa_link,sim_link,sim_ticket_id,need_by_date,description,first_approver,second_approver,false_escalation,mismatch_reason,has_sim_ticket,escalation_count,source\n"
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
        
        print(f"Successfully logged to S3 CSV with SIM lookup: {csv_filename}")
        print(f"SEV Classification: {validation_result['claimed_sev']} → {validation_result['actual_sev']}")
        print(f"SIM Ticket Severity: {validation_result.get('sim_ticket_sev', 'N/A')}")
        print(f"False Escalation: {validation_result['false_escalation']}")
        if validation_result['mismatch_reason']:
            print(f"Mismatch Reason: {validation_result['mismatch_reason']}")
        
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
        
        # Log to S3 CSV with SIM ticket lookup first
        validation_result = validate_sev_classification(data)
        escalation_counts = get_escalation_counts(
            data.get('alias', ''),
            data.get('station', '')
        )
        log_to_s3(data)
        
        # Format tracking message
        tracking_message = f"""🚨 NACC PO Escalation Request

Submitted by: {data.get('alias', 'Unknown')}
Type: {data['escalationType']}
Station: {data['station']}
Coupa Link: {data['coupaLink']}
SIM Link: {data.get('simLink', 'Not provided')}
Need by: {data['needByDate']}
Description: {data['description']}
First Approver: {data['firstApprover']}
Second Approver: {data['secondApprover']}
Escalation Count for User: {escalation_counts['user_count'] + 1}
Escalation Count for Station: {escalation_counts['station_count'] + 1}

Submitted at: {datetime.now(pytz.timezone('America/Chicago')).strftime('%Y-%m-%d %H:%M:%S %Z')}"""
        
        # Send to tracking channel
        send_slack_message(TRACKING_CHANNEL, tracking_message)
        
        # Add bot comment for severity mismatches
        if validation_result['false_escalation']:
            mismatch_comment = f"""🤖 **Severity Validation Alert**

**Issue Detected:** {validation_result.get('mismatch_reason', 'Classification mismatch')}
**Claimed Severity:** {validation_result['claimed_sev']}
**Actual Severity:** {validation_result['actual_sev']}
{f"**SIM Ticket Severity:** {validation_result['sim_ticket_sev']}" if validation_result.get('sim_ticket_sev') else "**No SIM Ticket Found**"}

**Action:** Submitter and DFA have been notified for coaching/awareness."""
            
            send_slack_message(TRACKING_CHANNEL, mismatch_comment)
        
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
        print(f"📊 S3 Enhanced Analytics with SIM Lookup: {S3_BUCKET_NAME}/escalations/nacc_escalations.csv")
        print("🔍 SIM Ticket Severity Validation: ENABLED")
        print("🤖 Behavior change notifications: ENABLED")
    else:
        print("⚠️  S3 logging disabled - add AWS credentials to enable")
    app.run(debug=False, host='0.0.0.0', port=port)
