#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
image_tag=${IMAGE_TAG:-material-checklist-dify:1.0}
[[ "$image_tag" =~ ^[a-zA-Z0-9][a-zA-Z0-9_./:-]*$ ]] || exit 1
mkdir -p "$script_dir/dist"
image_file=$(mktemp "$script_dir/dist/.image.XXXXXX.tar.gz")
trap 'rm -f -- "$image_file"' EXIT
package_args=()
build_args=()
for argument in "$@"; do
    case "$argument" in
        --without-workflow|--no-split) package_args+=("$argument") ;;
        *) build_args+=("$argument") ;;
    esac
done
docker build --platform linux/amd64 --provenance=false -t "$image_tag" "${build_args[@]}" .
docker save "$image_tag" | gzip > "$image_file"
python3 -B tools/build_delivery.py --image "$image_file" --image-tag "$image_tag" "${package_args[@]}"
