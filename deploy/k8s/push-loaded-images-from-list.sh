#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
read_image
docker image inspect "$IMAGE" >/dev/null
say "推送 $IMAGE"
docker push "$IMAGE"
say '镜像推送完成'
