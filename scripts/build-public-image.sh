#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=${TRACK_ANYWHERE_IMAGE:-ghcr.io/vibeanyorg/track-anywhere-api}
TAG=${TRACK_ANYWHERE_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}
PLATFORMS=${TRACK_ANYWHERE_IMAGE_PLATFORMS:-linux/amd64,linux/arm64}

cd "$ROOT"

docker buildx build \
  --platform "$PLATFORMS" \
  --target api-runtime \
  --tag "$IMAGE:$TAG" \
  --push \
  .

printf '%s\n' "$IMAGE:$TAG"
