#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE_REPO=${TRACK_ANYWHERE_STABLE_IMAGE_REPO:-track-anywhere-api}
GIT_SHA=$(cd "$ROOT" && git rev-parse --short HEAD)
DIRTY=0
if [ -n "$(cd "$ROOT" && git status --porcelain)" ]; then
  DIRTY=1
fi
DEFAULT_TAG="stable-$GIT_SHA"
if [ "$DIRTY" = "1" ]; then
  DEFAULT_TAG="$DEFAULT_TAG-dirty"
fi
IMAGE_TAG=${TRACK_ANYWHERE_STABLE_IMAGE_TAG:-$DEFAULT_TAG}

cd "$ROOT"
docker build \
  --target api-runtime \
  --label "track-anywhere.source-revision=$GIT_SHA" \
  --label "track-anywhere.source-dirty=$DIRTY" \
  --label "track-anywhere.image-purpose=stable-local-api" \
  -t "$IMAGE_REPO:$IMAGE_TAG" \
  -t "$IMAGE_REPO:stable" \
  .

printf 'Built %s:%s\n' "$IMAGE_REPO" "$IMAGE_TAG"
printf 'Updated floating local tag %s:stable\n' "$IMAGE_REPO"
