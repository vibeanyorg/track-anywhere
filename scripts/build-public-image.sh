#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
API_IMAGE=${TRACK_ANYWHERE_API_IMAGE:-ghcr.io/vibeanyorg/track-anywhere-api}
WEB_IMAGE=${TRACK_ANYWHERE_WEB_IMAGE:-ghcr.io/vibeanyorg/track-anywhere-web}
TAG=${TRACK_ANYWHERE_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}
PLATFORMS=${TRACK_ANYWHERE_IMAGE_PLATFORMS:-linux/amd64,linux/arm64}

cd "$ROOT"

docker buildx build \
  --platform "$PLATFORMS" \
  --target api-runtime \
  --tag "$API_IMAGE:$TAG" \
  --tag "$API_IMAGE:latest" \
  --push \
  .

docker buildx build \
  --platform "$PLATFORMS" \
  --target web-runtime \
  --tag "$WEB_IMAGE:$TAG" \
  --tag "$WEB_IMAGE:latest" \
  --push \
  .

printf '%s\n' "$API_IMAGE:$TAG"
printf '%s\n' "$API_IMAGE:latest"
printf '%s\n' "$WEB_IMAGE:$TAG"
printf '%s\n' "$WEB_IMAGE:latest"
