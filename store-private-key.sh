#!/usr/bin/env bash
set -euo pipefail
# store-private-key.sh
# Safely store the private key (plain + base64) into SSM Parameter Store as SecureString.
# Usage: REGION=sa-east-1 ./store-private-key.sh infra-shared-key-v1

KEY_FILE=${1:-infra-shared-key-v1}
REGION=${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-""}}}

if [[ -z "$REGION" ]]; then
  echo "[ERROR] REGION not set" >&2; exit 1; fi
if [[ ! -f "$KEY_FILE" ]]; then
  echo "[ERROR] File '$KEY_FILE' not found" >&2; exit 1; fi

# Ensure non-empty
if [[ ! -s "$KEY_FILE" ]]; then
  echo "[ERROR] File '$KEY_FILE' is empty" >&2; exit 1; fi

# Read full contents preserving newlines.
PRIVATE_KEY_CONTENT=$(cat "$KEY_FILE")
# Trim possible trailing carriage returns
PRIVATE_KEY_CONTENT=${PRIVATE_KEY_CONTENT//$'\r'/}

# Length check
if [[ ${#PRIVATE_KEY_CONTENT} -lt 32 ]]; then
  echo "[ERROR] Key content length seems too short (${#PRIVATE_KEY_CONTENT})" >&2; exit 1; fi

# Put plain (multiline) key
aws ssm put-parameter \
  --name /infra/cdk/ssh/private-key \
  --type SecureString \
  --overwrite \
  --value "$PRIVATE_KEY_CONTENT" \
  --region "$REGION" >/dev/null

echo "Stored plain private key at /infra/cdk/ssh/private-key"

# Also store base64 (no newlines)
if command -v base64 >/dev/null 2>&1; then
  KEY_B64=$(base64 < "$KEY_FILE")
  aws ssm put-parameter \
    --name /infra/cdk/ssh/private-key-b64 \
    --type SecureString \
    --overwrite \
    --value "$KEY_B64" \
    --region "$REGION" >/dev/null
  echo "Stored base64 private key at /infra/cdk/ssh/private-key-b64"
fi

echo "Verify retrieval (masked output expected):"
aws ssm get-parameter --with-decryption --name /infra/cdk/ssh/private-key --region "$REGION" --query 'Parameter.Type,Parameter.Name'

echo "Done. In CI you can retrieve either parameter; for base64 variant decode with: echo '$KEY_B64' | base64 -d > infra-shared-key-v1"
