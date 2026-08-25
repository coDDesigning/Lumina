#!/usr/bin/env bash
set -euo pipefail

: "${FRONTEND_BUCKET:?FRONTEND_BUCKET is required}"
: "${FRONTEND_DIST:?FRONTEND_DIST is required}"
: "${CLOUDFRONT_DISTRIBUTION_ID:?CLOUDFRONT_DISTRIBUTION_ID is required}"

content_type_for() {
  case "$1" in
    *.css) echo 'text/css' ;;
    *.html) echo 'text/html; charset=utf-8' ;;
    *.js) echo 'text/javascript' ;;
    *.jpg|*.jpeg) echo 'image/jpeg' ;;
    *.png) echo 'image/png' ;;
    *.svg) echo 'image/svg+xml' ;;
    *.webp) echo 'image/webp' ;;
    *.woff2) echo 'font/woff2' ;;
    *) echo 'application/octet-stream' ;;
  esac
}

current="s3://$FRONTEND_BUCKET/current"

while IFS= read -r -d '' file; do
  relative_path="${file#"$FRONTEND_DIST"/}"
  if [[ "$relative_path" == assets/* ]]; then
    cache_control='public,max-age=31536000,immutable'
  else
    cache_control='public,max-age=0,must-revalidate'
  fi
  aws s3 cp "$file" "$current/$relative_path" \
    --content-type "$(content_type_for "$file")" \
    --cache-control "$cache_control" --only-show-errors
done < <(find "$FRONTEND_DIST" -type f ! -path "$FRONTEND_DIST/index.html" -print0)

aws s3 cp "$FRONTEND_DIST/index.html" "$current/index.html" \
  --content-type 'text/html; charset=utf-8' \
  --cache-control 'no-cache,max-age=0,must-revalidate' --only-show-errors

invalidation_id="$(aws cloudfront create-invalidation \
  --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
  --paths '/*' \
  --query 'Invalidation.Id' --output text)"
aws cloudfront wait invalidation-completed \
  --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
  --id "$invalidation_id"

while IFS= read -r key; do
  relative_path="${key#current/}"
  if [ ! -f "$FRONTEND_DIST/$relative_path" ]; then
    aws s3api delete-object --bucket "$FRONTEND_BUCKET" --key "$key" >/dev/null
  fi
done < <(aws s3api list-objects-v2 \
  --bucket "$FRONTEND_BUCKET" --prefix 'current/' --output json |
  jq -r '.Contents[]?.Key')
