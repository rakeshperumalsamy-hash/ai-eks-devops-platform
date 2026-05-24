import boto3
import anthropic
import os
import json
import urllib.request
from datetime import datetime, timedelta

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SLACK_COST = os.environ.get("SLACK_COST")

def send_slack(message):
    data = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(SLACK_COST, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    print("✅ Slack alert sent to #cost-alerts!")

def get_aws_costs():
    print("💰 Fetching AWS costs...")
    ce = boto3.client('ce', region_name='ap-south-1')
    
    today = datetime.today()
    start = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    end = today.strftime('%Y-%m-%d')
    
    # Total cost
    response = ce.get_cost_and_usage(
        TimePeriod={'Start': start, 'End': end},
        Granularity='MONTHLY',
        Metrics=['UnblendedCost'],
        GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
    )
    
    costs = {}
    total = 0.0
    
    for result in response['ResultsByTime']:
        for group in result['Groups']:
            service = group['Keys'][0]
            amount = float(group['Metrics']['UnblendedCost']['Amount'])
            if amount > 0:
                costs[service] = round(amount, 2)
                total += amount
    
    return costs, round(total, 2)

def analyze_costs(costs, total):
    print("🤖 Claude analyzing costs...")
    
    # Top 5 services
    top_services = sorted(costs.items(), key=lambda x: x[1], reverse=True)[:5]
    
    prompt = f"""
You are an AWS cost optimization expert.

Last 30 days AWS costs:
Total: ${total}
Top services: {json.dumps(dict(top_services), indent=2)}

Provide:
1. Cost analysis — is this high/normal/low for EKS setup?
2. Which services are expensive and why
3. Cost optimization recommendations with exact steps
4. Estimated savings if recommendations followed
5. Alert level: HIGH (>$50), MEDIUM ($20-50), LOW (<$20)

Be specific and actionable for DevOps team.
"""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def check_costs():
    print("💰 AWS Cost Monitor Running...")
    print("="*50)
    
    costs, total = get_aws_costs()
    
    print(f"\n📊 Last 30 days total: ${total}")
    print("Top services:")
    for service, amount in sorted(costs.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {service}: ${amount}")
    
    analysis = analyze_costs(costs, total)
    
    print("\n" + "="*50)
    print("🤖 CLAUDE AI COST ANALYSIS")
    print("="*50)
    print(analysis)
    
    # Top 5 services for Slack
    top5 = sorted(costs.items(), key=lambda x: x[1], reverse=True)[:5]
    services_str = "\n".join([f"  • {s}: ${a}" for s, a in top5])
    
    emoji = "🔴" if total > 50 else "🟡" if total > 20 else "🟢"
    
    slack_msg = f"""
{emoji} *AWS COST REPORT — Last 30 Days*
*Total Spend:* ${total}

*Top Services:*
{services_str}

*🤖 Claude AI Analysis:*
{analysis[:1500]}
"""
    send_slack(slack_msg)

if __name__ == "__main__":
    check_costs()