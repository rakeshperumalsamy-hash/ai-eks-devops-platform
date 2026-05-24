import anthropic
import os
import subprocess
import json

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SLACK_UPGRADE = os.environ.get("SLACK_UPGRADE")

import urllib.request

def send_slack(message):
    data = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(SLACK_UPGRADE, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    print("✅ Slack alert sent to #upgrade-alerts!")

def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    return result.stdout.strip()

def get_current_versions():
    print("🔍 Scanning current environment...")
    
    k8s = run("kubectl version --client -o json")
    try:
        k8s_data = json.loads(k8s)
        k8s_version = k8s_data.get("clientVersion", {}).get("gitVersion", "unknown")
    except:
        k8s_version = "unknown"

    cluster = run("aws eks describe-cluster --name ai-cluster --region ap-south-1 --query cluster.version --output text")
    
    nodes = run("kubectl get nodes -o json")
    try:
        nodes_data = json.loads(nodes)
        node_versions = [n["status"]["nodeInfo"]["kubeletVersion"] for n in nodes_data.get("items", [])]
    except:
        node_versions = []

    addons = run("aws eks list-addons --cluster-name ai-cluster --region ap-south-1 --output json")
    try:
        addon_list = json.loads(addons).get("addons", [])
    except:
        addon_list = []

    addon_versions = {}
    for addon in addon_list:
        version = run(f"aws eks describe-addon --cluster-name ai-cluster --region ap-south-1 --addon-name {addon} --query addon.addonVersion --output text")
        addon_versions[addon] = version

    docker = run("docker version --format '{{.Client.Version}}'")

    return {
        "kubectl_version": k8s_version,
        "cluster_k8s_version": cluster,
        "node_versions": node_versions,
        "addon_versions": addon_versions,
        "docker_version": docker
    }

def analyze_upgrade(current_versions, target_version):
    print("🤖 Claude analyzing upgrade compatibility...")
    
    prompt = f"""
You are a Kubernetes upgrade expert.

Current Environment:
- Cluster K8s Version: {current_versions['cluster_k8s_version']}
- Node Versions: {current_versions['node_versions']}
- Addons: {json.dumps(current_versions['addon_versions'], indent=2)}
- Docker Version: {current_versions['docker_version']}

Target: Upgrade to Kubernetes {target_version}

Provide a detailed upgrade plan:
1. Prerequisites - what needs to be updated BEFORE upgrading K8s
2. Upgrade order - exact sequence of steps
3. Risks - what could go wrong
4. Solutions - how to fix each risk
5. Commands - exact commands for each step
6. Safe to upgrade now? YES/NO and why

Format clearly for DevOps team.
"""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def main():
    print("🚀 AI-Powered Upgrade Advisor")
    print("="*50)
    
    target = input("Target Kubernetes version (e.g. 1.31): ").strip()
    
    current = get_current_versions()
    
    print("\n📊 Current Environment:")
    print(f"  Cluster K8s: {current['cluster_k8s_version']}")
    print(f"  Node versions: {current['node_versions']}")
    print(f"  Docker: {current['docker_version']}")
    print(f"  Addons: {list(current['addon_versions'].keys())}")
    
    analysis = analyze_upgrade(current, target)
    
    print("\n" + "="*50)
    print("🤖 CLAUDE AI UPGRADE ANALYSIS")
    print("="*50)
    print(analysis)
    
    slack_msg = f"""
🔄 *UPGRADE ADVISOR REPORT*
*Current K8s:* {current['cluster_k8s_version']}
*Target K8s:* {target}
*Node versions:* {current['node_versions']}
*Addons:* {list(current['addon_versions'].keys())}

*🤖 Claude AI Analysis:*
{analysis[:2000]}
"""
    send_slack(slack_msg)

if __name__ == "__main__":
    main()