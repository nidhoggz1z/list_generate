#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
init_cluster
check_owner deployment "$APP"
k rollout status "deployment/$APP" --timeout=120s
k exec "deployment/$APP" -- python -c '
import json, urllib.request
from gateway.config import Settings
s=Settings.from_env(); s.require_dify()
base="http://127.0.0.1:8000"
def get(path):
    with urllib.request.urlopen(base+path, timeout=30) as r: return json.load(r)
h=get("/health")
assert h["status"]=="healthy" and h["service"]=="material-checklist-gateway"
files=get("/v1/checklist-files")["files"]; assert files
request=urllib.request.Request(base+"/v1/checklist-files/content", data=json.dumps({"fileName":files[0]["fileName"]}).encode(), headers={"Content-Type":"application/json"})
with urllib.request.urlopen(request,timeout=30) as r: assert json.load(r)["content"]
schema=get("/openapi.json")
assert any(s["url"]=="/material-checklist" for s in schema["servers"])
print("PASS：配置、健康、规则目录、规则正文、OpenAPI 前缀")
'
k get ingress "$APP"
if [[ -n "${ENTRY_URL:-}" ]]; then
    curl --fail --silent --show-error --max-time 30 "${ENTRY_URL%/}/health"
    curl --fail --silent --show-error --max-time 30 "${ENTRY_URL%/}/v1/checklist-files"
else
    say '外部入口未自动检查；设置 ENTRY_URL=http://<目标环境入口IP>:30100/material-checklist 后重跑 verify.sh'
fi
