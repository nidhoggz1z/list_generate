#!/usr/bin/env bash
# 联网机只准备源码和 Linux wheel，不构建镜像、不需要 Docker。
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
VERSION=${VERSION:-v1}
[[ $# == 0 ]] || { echo '用法：VERSION=<发布版本> bash deploy/k8s/prepare-bank.sh' >&2; exit 1; }
[[ "$VERSION" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || { echo '版本号无效' >&2; exit 1; }
[[ ! -e "$ROOT/dist/material-checklist-bank-$VERSION.tar.gz" ]] || { echo '准备包已存在，请先备份移走' >&2; exit 1; }
python3 -B "$ROOT/tools/build_k8s_delivery.py" --check-rules
WHEELS=$(mktemp -d "${TMPDIR:-/tmp}/material-checklist-wheels.XXXXXX")
trap 'rm -rf -- "$WHEELS"' EXIT
python3 -m pip download -r "$ROOT/gateway/requirements.txt" \
    --only-binary=:all: --python-version 3.12 --implementation cp --abi cp312 \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 --platform linux_x86_64 --dest "$WHEELS"
# 从采集结果离线解析一次，确认目标平台的依赖闭包完整。
python3 -m pip download -r "$ROOT/gateway/requirements.txt" \
    --no-index --find-links "$WHEELS" --only-binary=:all: \
    --python-version 3.12 --implementation cp --abi cp312 \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 --platform linux_x86_64 --dest "$WHEELS"
python3 -B "$ROOT/tools/build_k8s_delivery.py" --prepare-bank --version "$VERSION" --wheelhouse "$WHEELS"
