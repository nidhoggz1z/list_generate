#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "$0")" && pwd)/deploy-common.sh"
[[ -f "$deploy_dir/gateway.env" && -d "$deploy_dir/rules" ]] || { echo "Run release.sh first: gateway.env or rules missing" >&2; exit 1; }
docker image inspect "$image_tag" >/dev/null
check_owned "$gateway_name"
check_owned "$rules_name"
mkdir -p "$deploy_dir/gateway_storage"
for name in "$gateway_name" "$rules_name"; do
    if docker container inspect "$name" >/dev/null 2>&1; then docker rm -f "$name" >/dev/null; fi
done
docker run -d --name "$gateway_name" --label "com.material-checklist.owner=$app_name" \
    --restart unless-stopped --log-driver json-file --log-opt max-size=20m --log-opt max-file=3 \
    -p "$api_port:8000" -e TASK_STORAGE_ROOT=/app/gateway_storage \
    --mount "type=bind,source=$deploy_dir/gateway.env,target=/app/gateway.env,readonly" \
    --mount "type=bind,source=$deploy_dir/gateway_storage,target=/app/gateway_storage" \
    --mount "type=bind,source=$deploy_dir/rules,target=/app/rules,readonly" \
    "$image_tag"
printf 'CP input: http://SERVER:%s/api/v1/tasks\n' "$api_port"
printf 'Workflow catalog: http://SERVER:%s/v1/checklist-files\n' "$api_port"
printf 'Workflow content: http://SERVER:%s/v1/checklist-files/content\n' "$api_port"
