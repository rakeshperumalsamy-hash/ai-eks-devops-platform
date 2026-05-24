import anthropic
import os
import time
import urllib.request
import json
import ssl
import subprocess

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SLACK_CHANNELS = {
    "pod":         os.environ.get("SLACK_POD"),
    "performance": os.environ.get("SLACK_PERFORMANCE"),
    "cost":        os.environ.get("SLACK_COST"),
    "upgrade":     os.environ.get("SLACK_UPGRADE"),
    "critical":    os.environ.get("SLACK_CRITICAL")
}

KUBE_HOST = os.environ.get("KUBERNETES_SERVICE_HOST")
KUBE_PORT = os.environ.get("KUBERNETES_SERVICE_PORT")
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

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

def get_nodes():
    data = kube_request("/api/v1/nodes")
    return data.get("items", [])

def get_pod_logs(namespace, pod_name):
    try:
        data = kube_request(f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log?tailLines=50")
        return str(data)
    except:
        return "Logs unavailable"

def send_slack_alert(channel_type, title, fields, analysis, severity="LOW"):
    emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(severity, "🟡")
    webhook = SLACK_CHANNELS.get(channel_type, SLACK_CHANNELS["pod"])
    
    field_blocks = [{"type": "mrkdwn", "text": f"*{k}:*\n{v}"} for k, v in fields.items()]
    
    message = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {title}"}
            },
            {
                "type": "section",
                "fields": field_blocks
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*🤖 Claude AI Analysis:*\n{analysis}"}
            }
        ]
    }
    data = json.dumps(message).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    print(f"✅ Alert sent to #{channel_type}!")

def analyze_with_claude(prompt):
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# ─── POD MONITOR ───
alerted_pods = set()

def check_pod_issues():
    pods = get_pods()
    for pod in pods:
        name = pod["metadata"]["name"]
        namespace = pod["metadata"]["namespace"]
        if "ai-eks-monitor" in name:
            continue
        phase = pod["status"].get("phase", "")
        container_statuses = pod["status"].get("containerStatuses", [])
        
        status = None
        if phase == "Pending":
            status = "Pending"
        for cs in container_statuses:
            waiting = cs.get("state", {}).get("waiting", {})
            reason = waiting.get("reason", "")
            if reason in ["CrashLoopBackOff", "Error", "OOMKilled"]:
                status = reason

        if status and name not in alerted_pods:
            print(f"⚠️ Pod issue: {name} - {status}")
            logs = get_pod_logs(namespace, name)
            prompt = f"""
            Kubernetes pod '{name}' has issue: {status}
            Logs: {logs}
            Provide:
            1. Root cause
            2. Severity (HIGH/MEDIUM/LOW)
            3. Solution with exact commands
            """
            analysis = analyze_with_claude(prompt)
            severity = "HIGH" if status == "CrashLoopBackOff" else "MEDIUM"
            channel = "critical" if severity == "HIGH" else "pod"
            send_slack_alert(
                channel,
                "Pod Alert Detected!",
                {"Pod": name, "Status": status, "Namespace": namespace},
                analysis,
                severity
            )
            alerted_pods.add(name)

# ─── PERFORMANCE MONITOR ───
def check_performance():
    try:
        metrics = kube_request("/apis/metrics.k8s.io/v1beta1/pods")
        for item in metrics.get("items", []):
            name = item["metadata"]["name"]
            namespace = item["metadata"]["namespace"]
            for container in item.get("containers", []):
                cpu = container["usage"].get("cpu", "0")
                memory = container["usage"].get("memory", "0")
                
                cpu_val = int(cpu.replace("n", "")) / 1000000 if "n" in cpu else 0
                mem_val = int(memory.replace("Ki", "")) / 1024 if "Ki" in memory else 0
                
                if cpu_val > 800 or mem_val > 400:
                    print(f"⚠️ High resource: {name} CPU:{cpu_val}m MEM:{mem_val}MB")
                    prompt = f"""
                    Pod '{name}' high resource usage:
                    CPU: {cpu_val}m (threshold: 800m)
                    Memory: {mem_val}MB (threshold: 400MB)
                    
                    Provide:
                    1. Why this might be happening
                    2. Severity (HIGH/MEDIUM/LOW)
                    3. Solution with commands
                    """
                    analysis = analyze_with_claude(prompt)
                    severity = "HIGH" if cpu_val > 900 or mem_val > 450 else "MEDIUM"
                    send_slack_alert(
                        "performance",
                        "High Resource Usage!",
                        {"Pod": name, "CPU": f"{cpu_val}m", "Memory": f"{mem_val}MB", "Namespace": namespace},
                        analysis,
                        severity
                    )
    except Exception as e:
        print(f"Performance check error: {e}")

# ─── LOG ANOMALY MONITOR ───
ERROR_PATTERNS = ["ERROR", "FATAL", "Exception", "Traceback", "OOMKilled", "panic", "CRITICAL", "timeout"]
anomaly_alerted = {}

def check_log_anomaly():
    try:
        pods = get_pods()
        for pod in pods:
            name = pod["metadata"]["name"]
            namespace = pod["metadata"]["namespace"]
            if "ai-eks-monitor" in name:
                continue
            logs = get_pod_logs(namespace, name)
            if not logs:
                continue
            for pattern in ERROR_PATTERNS:
                if pattern.upper() in logs.upper():
                    last = anomaly_alerted.get(name, 0)
                    if time.time() - last > 3600:
                        print(f"⚠️ Log anomaly in {name}: {pattern}")
                        prompt = f"Pod '{name}' has '{pattern}' in logs:\n{logs[-1000:]}\nProvide: 1.Root cause 2.Severity 3.Fix commands"
                        analysis = analyze_with_claude(prompt)
                        send_slack_alert("critical", "Log Anomaly!", {"Pod": name, "Pattern": pattern, "Namespace": namespace}, analysis, "HIGH")
                        anomaly_alerted[name] = time.time()
                    break
    except Exception as e:
        print(f"Log anomaly error: {e}")

# ─── MAIN MONITOR ───
def monitor():
    print("🔍 AI-Powered DevOps Platform Starting...")
    print("📡 Multi-channel Slack alerts enabled!")
    print("Monitoring: Pods + Performance + Log Anomaly\n")
    
    counter = 0
    while True:
        print(f"--- Check #{counter+1} ---")
        check_pod_issues()
        
        if counter % 2 == 0:
            check_performance()
        
        if counter % 3 == 0:
            check_log_anomaly()
            
        print("Checking again in 30 seconds...\n")
        time.sleep(30)
        counter += 1

if __name__ == "__main__":
    monitor()