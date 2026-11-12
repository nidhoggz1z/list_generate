#!/usr/bin/env bash
# 在 11.33 使用麒麟基座构建镜像并打包；源码包自带 VERSION 和离线 wheel。
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ -z "${VERSION:-}" ]]; then
    if [[ -f "$ROOT/BANK_VERSION" ]]; then
        VERSION=$(cat "$ROOT/BANK_VERSION")
    else
        VERSION=$(git -C "$ROOT" rev-parse --short=12 HEAD)
    fi
fi
BANK_BASE=${BANK_BASE:-10.208.11.33/k8s/kylinv10sp3-python312-ssh:latest}
[[ $# == 0 ]] || { echo '用法：VERSION=<发布版本> bash deploy/k8s/make-package.sh' >&2; exit 1; }
[[ "$VERSION" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || { echo '版本号无效' >&2; exit 1; }
IMAGE="10.208.11.33/agents/shb/material-checklist-dify:$VERSION"
OUTPUT="$ROOT/dist/k8s-$VERSION"
[[ ! -e "$OUTPUT" ]] || { echo "输出已存在，请先备份移走：$OUTPUT" >&2; exit 1; }
shopt -s nullglob
RULES=("$ROOT"/rules/*材料清单.docx)
[[ ${#RULES[@]} -gt 0 && -f "$ROOT/rules/话术库对应材料.xlsx" ]] || { echo '缺少根目录 rules/ 规则资料' >&2; exit 1; }
WHEELS=("$ROOT"/offline/wheelhouse/*.whl)
[[ ${#WHEELS[@]} -gt 0 ]] || { echo '缺少离线依赖，请先在联网机运行 prepare-bank.sh，并上传准备包到 11.33' >&2; exit 1; }
mkdir -p "$ROOT/dist"
SAVED_IMAGE=$(mktemp "$ROOT/dist/.k8s-image.XXXXXX")
trap 'rm -f -- "$SAVED_IMAGE"' EXIT
echo "==> 麒麟基座：$BANK_BASE"
docker build -f "$ROOT/Dockerfile.bank" --build-arg "BANK_BASE=$BANK_BASE" -t "$IMAGE" "$ROOT"
# 按最终运行用户冒烟；基座 Python 目录需要补充 root 组读取权限。
docker run --rm --group-add 0 --entrypoint /app/.venv/bin/python "$IMAGE" \
    -c 'import ssl, sqlite3, fastapi, httpx, uvicorn; from gateway.main import app; print("麒麟镜像导入检查通过")'
docker save "$IMAGE" | gzip > "$SAVED_IMAGE"
# 使用产物内的 Python 3.12 打包，不依赖 11.33 宿主机的 Python 版本。
docker run --rm --user "$(id -u):$(id -g)" --group-add 0 \
    --entrypoint /app/.venv/bin/python -v "$ROOT:/workspace" -w /workspace "$IMAGE" \
    -B tools/build_k8s_delivery.py --image "dist/$(basename "$SAVED_IMAGE")" --version "$VERSION"
