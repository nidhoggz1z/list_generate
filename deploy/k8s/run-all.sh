#!/usr/bin/env bash
# 现场入口：bash /haw/lwh-install/run-all.sh
set -euo pipefail
umask 077
INSTALL_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$INSTALL_DIR"
[[ $# == 0 ]] || { echo '用法：bash run-all.sh（无需参数）' >&2; exit 1; }
command -v kubectl >/dev/null
command -v sha256sum >/dev/null
export KUBE_CONTEXT=${KUBE_CONTEXT:-$(kubectl config current-context)}
echo "==> 使用 context=${KUBE_CONTEXT}，部署到 agent2"
[[ -f material-checklist-deploy.tar.gz && -f SHA256SUMS ]] || {
    echo '缺少 material-checklist-deploy.tar.gz 或 SHA256SUMS' >&2; exit 1;
}
sha256sum -c SHA256SUMS
if [[ ! -d material-checklist-deploy ]]; then
    tar -xzf material-checklist-deploy.tar.gz
fi
for script in install-images.sh deploy-all.sh verify.sh; do
    [[ -f "material-checklist-deploy/$script" ]] || { echo "部署包缺少 $script" >&2; exit 1; }
done
bash material-checklist-deploy/install-images.sh
bash material-checklist-deploy/deploy-all.sh
echo "==> 部署完成。验证：bash $INSTALL_DIR/material-checklist-deploy/verify.sh"
