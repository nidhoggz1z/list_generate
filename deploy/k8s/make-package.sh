#!/usr/bin/env bash
# 开发机构建并打包；默认读取项目根目录 rules/。
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
VERSION=${VERSION:-$(git -C "$ROOT" rev-parse --short=12 HEAD)}
[[ $# == 0 ]] || { echo '用法：VERSION=<发布版本> bash deploy/k8s/make-package.sh' >&2; exit 1; }
[[ "$VERSION" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || { echo '版本号无效' >&2; exit 1; }
IMAGE="10.208.11.33/agents/shb/material-checklist-dify:$VERSION"
OUTPUT="$ROOT/dist/k8s-$VERSION"
[[ ! -e "$OUTPUT" ]] || { echo "输出已存在，请先备份移走：$OUTPUT" >&2; exit 1; }
# 构建前先检查规则文件，避免构建完成后才发现缺少资料。
python3 -B "$ROOT/tools/build_k8s_delivery.py" --check-rules
mkdir -p "$ROOT/dist"
SAVED_IMAGE=$(mktemp "$ROOT/dist/.k8s-image.XXXXXX")
trap 'rm -f -- "$SAVED_IMAGE"' EXIT
docker build --platform linux/amd64 --provenance=false -t "$IMAGE" "$ROOT"
docker save "$IMAGE" | gzip > "$SAVED_IMAGE"
python3 -B "$ROOT/tools/build_k8s_delivery.py" --image "$SAVED_IMAGE" --version "$VERSION"
