#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "$0")" && pwd)/deploy-common.sh"
check_owned "$gateway_name"
check_owned "$rules_name"
for name in "$gateway_name" "$rules_name"; do
    if docker container inspect "$name" >/dev/null 2>&1; then docker stop "$name"; fi
done
