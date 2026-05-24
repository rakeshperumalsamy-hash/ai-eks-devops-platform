import anthropic
import os
import time
import json
import urllib.request
import ssl

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SLACK_CRITICAL = os.environ.get("SLACK_CRITICAL")

KUBE_HOST = os.environ.get("KUBERNETES_SERVICE_HOST")
KUBE_PORT = os.environ.get("KUBERNETES_SERVICE_PORT")
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

# Error patterns to watch
ERROR_PATTERNS = [
    "ERROR", "FATAL", "Exception", "Traceback",
    "OOMKilled", "panic", "CRITICAL", "ConnectionRefused",
    "timeout", "OutOfMemory", "killed"
]

def send_slack(message):
    data = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_CRITICAL, data=data,
        headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req)
    print("✅ Alert sent to #critical!")

def get_token():
    with open(TOKEN_PATH) as f:
        return f.read().strip()

def kube_request(path):
    token = get_token()
    url = f"https://{KUBE_HOST}:{KUBE_PORT}{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    ctx = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(req, context=ctx) as resp:
        return json.loads(resp.read())

def get_pods():
    data = kube_request("/api/v1/pods")
    return data.get("items", [])

def get_pod_logs(namespace, pod_name):
    try:
        data = kube_request(
            f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log?tailLines=100"
        )
        return str(data)
    except:
        return ""

def has_anomaly(logs):
    logs_upper = logs.upper()
    for pattern in ERROR_PATTERNS:
        if pattern.upper() in logs_upper:
            return True, pattern
    return False, None

def analyze_anomaly(pod_name, logs, pattern):
    print(f"🤖 Claude analyzing anomaly in {pod_name}...")
    prompt = f"""
You are a DevOps expert analyzing Kubernetes pod logs.

Pod: {pod_name}
Detected pattern: {pattern}

Recent logs:
{logs[-2000:]}

Provide:
1. What is the anomaly?
2. Severity (HIGH/MEDIUM/LOW)
3. Root cause
4. Immediate fix with exact commands
5. Prevention steps
"""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

alerted_pods = {}

def check_logs():
    pods = get_pods()
    for pod in pods:
        name = pod["metadata"]["name"]
        namespace = pod["metadata"]["namespace"]

        if "ai-eks-monitor" in name:
            continue

        logs = get_pod_logs(namespace, name)
        if not logs:
            continue

        has_error, pattern = has_anomaly(logs)

        if has_error:
            last_alert = alerted_pods.get(name, 0)
            if time.time() - last_alert > 3600:  # 1 hour cooldown
                print(f"⚠️ Anomaly in {name}: {pattern}")
                analysis = analyze_anomaly(name, logs, pattern)

                slack_msg = f"""
🚨 *LOG ANOMALY DETECTED*
*Pod:* {name}
*Namespace:* {namespace}
*Pattern:* `{pattern}`

*🤖 Claude AI Analysis:*
{analysis[:1500]}
"""
                send_slack(slack_msg)
                alerted_pods[name] = time.time()

def monitor():
    print("🔍 Log Anomaly Detection Starting...")
    print("📡 Monitoring all pod logs 24/7...")
    print("="*50)

    while True:
        try:
            check_logs()
            print("✅ Log check complete!")
        except Exception as e:
            print(f"❌ Error: {e}")

        print("Checking again in 60 seconds...\n")
        time.sleep(60)

if __name__ == "__main__":
    monitor()