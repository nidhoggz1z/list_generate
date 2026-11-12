#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
UPDATE_RULES=false
case "${1:-}" in
    '') [[ $# == 0 ]] || fail '参数无效' ;;
    --update-rules) [[ $# == 1 ]] || fail '参数无效'; UPDATE_RULES=true ;;
    *) fail '用法：bash deploy-all.sh [--update-rules]' ;;
esac
init_cluster
read_image
MANIFEST="$SCRIPT_DIR/k8s/material-checklist.yaml"
if grep -Eq 'DIFY_API_(URL|KEY):.*(<|FIXME_)' "$MANIFEST"; then
    fail '请先填写 k8s/material-checklist.yaml 中的 HiAgent URL 和 API Key，再重跑本脚本'
fi
k get namespace "$NS" --ignore-not-found -o name >/dev/null
for resource in 'deployment material-checklist-gateway' 'service material-checklist-gateway' 'ingress material-checklist-gateway' 'secret material-checklist-config'; do
    read -r kind name <<< "$resource"
    check_owner "$kind" "$name"
done
EXISTING=$(k get deployment "$APP" --ignore-not-found -o name)
NODE=''
if [[ -n "$EXISTING" ]]; then
    NODE=$(k get deployment "$APP" -o 'jsonpath={.spec.template.spec.nodeSelector.kubernetes\.io/hostname}')
    [[ -n "$NODE" ]] || fail '已有部署没有固定节点，拒绝自动选择其他节点'
fi
if [[ -f "$SCRIPT_DIR/.node-name" ]]; then
    SAVED_NODE=$(cat "$SCRIPT_DIR/.node-name")
    [[ -z "$NODE" || "$NODE" == "$SAVED_NODE" ]] || fail '现场记录与已有部署的节点不一致'
    NODE=$SAVED_NODE
fi
if [[ -z "$NODE" ]]; then
    NODE=$(k get nodes -l '!node-role.kubernetes.io/control-plane,!node-role.kubernetes.io/master' \
        -o 'jsonpath={range .items[*]}{.metadata.name}{"|"}{.spec.unschedulable}{"|"}{.status.nodeInfo.architecture}{"|"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
        | awk -F '|' '$2 != "true" && $3 == "amd64" && $4 == "True" {print $1; exit}')
fi
[[ "$NODE" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || fail '未找到可用的 amd64 工作节点'
NODE_STATE=$(k get node "$NODE" -o 'jsonpath={.spec.unschedulable}{"|"}{.status.nodeInfo.architecture}{"|"}{.status.conditions[?(@.type=="Ready")].status}')
[[ "$NODE_STATE" == '|amd64|True' || "$NODE_STATE" == 'false|amd64|True' ]] || fail "固定节点 $NODE 不可用，不会自动迁移数据"
mkdir -p "$SCRIPT_DIR/.runtime"
printf '%s\n' "$NODE" > "$SCRIPT_DIR/.node-name"
say "固定数据节点：$NODE"
RULES_SHA=$(sha "$SCRIPT_DIR/rules.sha256")
if [[ -n "$EXISTING" && "$UPDATE_RULES" == false ]]; then
    RULES_SHA=$(k get deployment "$APP" -o 'jsonpath={.spec.template.metadata.annotations.material-checklist/rules-sha}')
    [[ -n "$RULES_SHA" ]] || fail '已有部署缺少规则版本记录，请核对后使用 --update-rules'
fi
[[ "$RULES_SHA" =~ ^[a-f0-9]{64}$ ]] || fail '规则校验记录无效'
sed -e "s/FIXME_WORKER/$NODE/g" -e "s/FIXME_RULES_SHA/$RULES_SHA/g" "$MANIFEST" > "$SCRIPT_DIR/.runtime/pending.yaml"
RELEASE_SHA=$(sha "$SCRIPT_DIR/.runtime/pending.yaml")
sed "s/FIXME_MANIFEST_SHA/$RELEASE_SHA/g" "$SCRIPT_DIR/.runtime/pending.yaml" > "$SCRIPT_DIR/.runtime/material-checklist.yaml"
RENDERED="$SCRIPT_DIR/.runtime/material-checklist.yaml"
[[ "$(awk '$1 == "image:" {print $2}' "$RENDERED" | sort -u)" == "$IMAGE" ]] || fail '服务 YAML 镜像与交付清单不一致'
OLD_SHA=''
if [[ -n "$EXISTING" ]]; then
    OLD_SHA=$(k get deployment "$APP" -o 'jsonpath={.spec.template.metadata.annotations.material-checklist/manifest-sha}')
    k apply --dry-run=server -f "$RENDERED" >/dev/null
fi
if [[ -n "$EXISTING" && "$OLD_SHA" != "$RELEASE_SHA" ]]; then
    REPLICAS=$(k get deployment "$APP" -o 'jsonpath={.spec.replicas}')
    if [[ "$REPLICAS" != 0 ]]; then
        say '升级前请确保调用方已暂停新任务；检查队列是否排空'
        k exec "deployment/$APP" -- python -c 'import json, urllib.request; h=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=10)); q=h["tasks"]; assert not(q.get("PENDING",0) or q.get("PROCESSING",0)), "仍有未完成任务，停止升级"'
    fi
    BACKUP="$SCRIPT_DIR/backups/$(date -u +%Y%m%dT%H%M%S)-$$"
    mkdir -p "$BACKUP"
    k get deployment,service,ingress "$APP" -o yaml > "$BACKUP/resources.yaml"
    k get secret material-checklist-config -o yaml > "$BACKUP/secret.yaml"
    say "配置备份：$BACKUP"
    k scale "deployment/$APP" --replicas=0
    k wait --for=delete pod -l "app=$APP" --timeout=180s
fi
k apply -f "$SCRIPT_DIR/k8s/00-namespace.yaml" >/dev/null
if [[ -z "$EXISTING" ]]; then
    k apply --dry-run=server -f "$RENDERED" >/dev/null
fi
if [[ -z "$EXISTING" || "$OLD_SHA" != "$RELEASE_SHA" ]]; then
    # A unique helper name avoids touching other installations' Pods.
    HELPER="material-checklist-prepare-$$-$RANDOM"
    HELPER_CREATED=false
    cleanup() {
        if [[ "$HELPER_CREATED" == true ]]; then
            k delete pod "$HELPER" --ignore-not-found --wait=false >/dev/null 2>&1 || true
        fi
    }
    trap cleanup EXIT
    k create -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $HELPER
  namespace: agent2
  labels:
    app.kubernetes.io/part-of: material-checklist
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  nodeSelector:
    kubernetes.io/hostname: $NODE
  containers:
  - name: prepare
    image: $IMAGE
    command: [python, -c, "import time; time.sleep(1800)"]
    resources:
      requests: {cpu: 10m, memory: 64Mi}
      limits: {cpu: "1", memory: 512Mi}
    volumeMounts:
    - name: data
      mountPath: /data
  volumes:
  - name: data
    hostPath:
      path: /haw/shb/data/material-checklist
      type: DirectoryOrCreate
EOF
    HELPER_CREATED=true
    k wait --for=condition=Ready "pod/$HELPER" --timeout=300s
    PREPARE_ARGS=()
    [[ -z "$EXISTING" ]] || PREPARE_ARGS+=(--backup)
    [[ "$UPDATE_RULES" == false ]] || PREPARE_ARGS+=(--update-rules)
    (cd "$SCRIPT_DIR" && sha256sum -c rules.sha256 >/dev/null)
    tar -C "$SCRIPT_DIR/rules" -cf - . | k exec -i "$HELPER" -- python -c "$(cat "$SCRIPT_DIR/bootstrap.py")" "${PREPARE_ARGS[@]}"
    cleanup
    trap - EXIT
fi
k apply -f "$RENDERED" >/dev/null
k rollout status "deployment/$APP" --timeout=900s
k exec "deployment/$APP" -- python -c 'from gateway.config import Settings; from service.app import RuleStore; s=Settings.from_env(); s.require_dify(); print("规则数：",len(RuleStore(s.rules_dir).files()))'
k get pods -l "app=$APP" -o wide
k logs "deployment/$APP" --tail=30
say '入口：http://<SIT入口IP>:30100/material-checklist'
say '业务日志：容器 /app/gateway_storage/log/，节点 /haw/shb/data/material-checklist/gateway_storage/log/'
