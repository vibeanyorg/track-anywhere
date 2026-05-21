#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=${TRACK_ANYWHERE_IMAGE:-ghcr.io/vibeanyorg/track-anywhere}
TAG=${TRACK_ANYWHERE_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}
PLATFORMS=${TRACK_ANYWHERE_IMAGE_PLATFORMS:-linux/amd64,linux/arm64}

cd "$ROOT"

docker buildx build \
  --platform "$PLATFORMS" \
  --tag "$IMAGE:$TAG" \
  --tag "$IMAGE:latest" \
  --push \
  .

printf '%s\n' "$IMAGE:$TAG"
printf '%s\n' "$IMAGE:latest"
