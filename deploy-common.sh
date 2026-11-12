#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
app_name=${APP_NAME:-material-checklist-dify}
deploy_dir=${DEPLOY_DIR:-/data/deploy_material-checklist-dify}
api_port=${API_PORT:-18008}
image_tag=${IMAGE_TAG:-$(cat "$script_dir/image-name.txt" 2>/dev/null || printf 'material-checklist-dify:1.0')}
[[ "$app_name" =~ ^[a-z0-9][a-z0-9_.-]*$ ]] || { echo "Invalid APP_NAME" >&2; exit 1; }
[[ "$image_tag" =~ ^[a-zA-Z0-9][a-zA-Z0-9_./:-]*$ ]] || { echo "Invalid IMAGE_TAG" >&2; exit 1; }
[[ "$deploy_dir" == /* && "$deploy_dir" != / && "$deploy_dir" != *','* ]] || { echo "DEPLOY_DIR must be a non-root absolute path without commas" >&2; exit 1; }
[[ "$api_port" =~ ^[1-9][0-9]{0,4}$ ]] && (( api_port <= 65535 )) || { echo "Invalid port" >&2; exit 1; }
gateway_name="$app_name-gateway"
rules_name="$app_name-rules"
check_owned() {
    local name="$1" owner
    if docker container inspect "$name" >/dev/null 2>&1; then
        owner=$(docker container inspect --format '{{ index .Config.Labels "com.material-checklist.owner" }}' "$name")
        [[ "$owner" == "$app_name" ]] || { echo "Refuse to replace unrelated container: $name" >&2; exit 1; }
    fi
}
