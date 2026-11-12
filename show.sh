#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "$0")" && pwd)/deploy-common.sh"
docker ps -a --filter "label=com.material-checklist.owner=$app_name" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
printf 'API port=%s; storage=%s/gateway_storage\n' "$api_port" "$deploy_dir"
