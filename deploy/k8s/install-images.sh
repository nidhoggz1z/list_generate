#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$SCRIPT_DIR/.."
read_image
if [[ ! -f material-checklist-images.tar.gz ]]; then
    shopt -s nullglob
    parts=(material-checklist-images.tar.gz.part-*)
    [[ ${#parts[@]} -gt 0 ]] || fail '未找到镜像整包或分片'
    cat "${parts[@]}" > material-checklist-images.tar.gz.tmp
    mv material-checklist-images.tar.gz.tmp material-checklist-images.tar.gz
fi
docker info >/dev/null
say '导入镜像'
docker load -i material-checklist-images.tar.gz
bash "$SCRIPT_DIR/push-loaded-images-from-list.sh"
