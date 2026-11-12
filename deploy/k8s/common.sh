#!/usr/bin/env bash
set -euo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
NS=agent2
APP=material-checklist-gateway
OWNER=material-checklist
say() { printf '==> %s\n' "$*"; }
fail() { printf '错误: %s\n' "$*" >&2; exit 1; }
sha() { sha256sum "$1" | awk '{print $1}'; }
init_cluster() {
    command -v kubectl >/dev/null || fail '未找到 kubectl'
    CONTEXT=${KUBE_CONTEXT:-$(kubectl config current-context)}
    [[ -n "$CONTEXT" ]] || fail '请先配置目标 SIT context'
    export KUBE_CONTEXT="$CONTEXT"
    say "context=$CONTEXT namespace=$NS"
}
k() { kubectl --context "$CONTEXT" --namespace "$NS" "$@"; }
check_owner() {
    local found
    found=$(k get "$1" "$2" --ignore-not-found -o 'jsonpath={.metadata.labels.app\.kubernetes\.io/part-of}')
    if [[ -z "$found" ]]; then
        [[ -z "$(k get "$1" "$2" --ignore-not-found -o name)" ]] || fail "$1/$2 没有本项目归属标签，拒绝接管"
    else
        [[ "$found" == "$OWNER" ]] || fail "$1/$2 属于其他项目，拒绝修改"
    fi
}
read_image() {
    IMAGE=$(awk 'NF && $1 !~ /^#/ {sub(/\r$/, ""); print}' "$SCRIPT_DIR/images-sit.txt")
    [[ "$IMAGE" =~ ^[a-zA-Z0-9][a-zA-Z0-9./:_-]+:[a-zA-Z0-9._-]+$ ]] || fail '镜像清单必须只有一个带 tag 的镜像'
}
