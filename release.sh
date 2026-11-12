#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "$0")" && pwd)/deploy-common.sh"
cd "$script_dir"
[[ -f image.tar.gz && -f SHA256SUMS && -f gateway.env.example && -d rules ]] || { echo "Incomplete offline package" >&2; exit 1; }
sha256sum -c SHA256SUMS
gzip -dc image.tar.gz | docker load
mkdir -p "$deploy_dir"
if [[ ! -e "$deploy_dir/gateway.env" ]]; then
    cp gateway.env.example "$deploy_dir/gateway.env"
    chmod 600 "$deploy_dir/gateway.env"
fi
if [[ ! -e "$deploy_dir/rules" ]]; then cp -a rules "$deploy_dir/rules"; fi
if [[ -d "$deploy_dir/rules" ]]; then
    first_rule_entry=$(find "$deploy_dir/rules/." -mindepth 1 -maxdepth 1 -print -quit)
    if [[ -z "$first_rule_entry" ]]; then
        # Copy into the existing directory so an active bind mount keeps its identity.
        cp -a rules/. "$deploy_dir/rules/"
        printf 'Initialized empty rules directory: %s\n' "$deploy_dir/rules"
    fi
fi
for name in deploy-common.sh start.sh stop.sh show.sh image-name.txt DEPLOYMENT.md; do
    if [[ "$script_dir" != "$(cd "$deploy_dir" && pwd)" ]]; then cp "$name" "$deploy_dir/$name"; fi
done
if [[ "$script_dir" != "$(cd "$deploy_dir" && pwd)" ]]; then
    for name in WORKFLOW_TEST.md docs/高码包装部署说明.md workflows/材料清单-重构.yml; do
        if [[ -f "$name" ]]; then
            mkdir -p "$deploy_dir/$(dirname -- "$name")"
            cp "$name" "$deploy_dir/$name"
        fi
    done
fi
chmod +x "$deploy_dir/"*.sh
printf 'Configuration and existing rules are preserved. Edit %s/gateway.env, then run bash %s/start.sh.\n' "$deploy_dir" "$deploy_dir"
