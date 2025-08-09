#!/usr/bin/env bash
set -euo pipefail

# add-public-key.sh
# Inject (idempotently) an SSH public key into all current FastAPI + Gateway ASG instances via SSM.
# Requirements: aws cli v2 configured, permissions for ec2, autoscaling, ssm. Key file present locally.

KEY_FILE=${1:-infra-shared-key-v1.pub}
REGION=${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-""}}}

if [[ -z "$REGION" ]]; then
  echo "[ERROR] REGION not set. Export REGION or configure a default (aws configure)." >&2
  exit 1
fi

if [[ ! -f "$KEY_FILE" ]]; then
  echo "[ERROR] Public key file '$KEY_FILE' not found." >&2
  exit 1
fi

PUB_KEY_CONTENT=$(tr -d '\r' < "$KEY_FILE")
if [[ -z "$PUB_KEY_CONTENT" ]]; then
  echo "[ERROR] Public key content is empty." >&2
  echo "Debug info:" >&2
  if [[ -f "$KEY_FILE" ]]; then
    echo "- File exists: $(ls -l "$KEY_FILE")" >&2
    echo "- File size bytes: $(wc -c < "$KEY_FILE" 2>/dev/null || echo 'n/a')" >&2
    echo "- Head (hex dump first 64 bytes):" >&2
    (command -v hexdump >/dev/null && hexdump -C "$KEY_FILE" | head -n 5) || echo "hexdump not available" >&2
    echo "- Cat with visible ends (cat -A):" >&2
    (command -v cat >/dev/null && cat -A "$KEY_FILE" || true) >&2
  else
    echo "- File does not actually exist at path provided" >&2
  fi
  echo "Tips: Ensure the .pub file contains a line like 'ssh-ed25519 AAAAC3... comment'." >&2
  echo "If missing, (re)generate with: ssh-keygen -t ed25519 -f infra-shared-key-v1 -C shared -N ''" >&2
  exit 1
fi

# Fetch ASG names from SSM parameters (source of truth set by CDK stack)
FASTAPI_ASG=$(aws ssm get-parameter --name /infra/cdk/fastapi/asg-name --query Parameter.Value --output text --region "$REGION")
GATEWAY_ASG=$(aws ssm get-parameter --name /infra/cdk/gateway/asg-name --query Parameter.Value --output text --region "$REGION")

if [[ "$FASTAPI_ASG" == "None" || -z "$FASTAPI_ASG" ]]; then
  echo "[ERROR] Could not resolve FastAPI ASG name from SSM." >&2; exit 1; fi
if [[ "$GATEWAY_ASG" == "None" || -z "$GATEWAY_ASG" ]]; then
  echo "[ERROR] Could not resolve Gateway ASG name from SSM." >&2; exit 1; fi

echo "FastAPI ASG: $FASTAPI_ASG"
echo "Gateway ASG: $GATEWAY_ASG"

# Collect healthy instance IDs (InService + Healthy) - portable (no mapfile)
FASTAPI_IDS_RAW=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$FASTAPI_ASG" \
  --query 'AutoScalingGroups[].Instances[?LifecycleState==`InService` && HealthStatus==`Healthy`].InstanceId' \
  --output text --region "$REGION" 2>/dev/null || true)
GATEWAY_IDS_RAW=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$GATEWAY_ASG" \
  --query 'AutoScalingGroups[].Instances[?LifecycleState==`InService` && HealthStatus==`Healthy`].InstanceId' \
  --output text --region "$REGION" 2>/dev/null || true)

# Normalize whitespace to newlines then read into arrays
FASTAPI_IDS=()
for x in $FASTAPI_IDS_RAW; do
  [[ -n "$x" ]] && FASTAPI_IDS+=("$x")
done
GATEWAY_IDS=()
for x in $GATEWAY_IDS_RAW; do
  [[ -n "$x" ]] && GATEWAY_IDS+=("$x")
done

IDS=("${FASTAPI_IDS[@]}" "${GATEWAY_IDS[@]}")
if [[ ${#IDS[@]} -eq 0 ]]; then
  echo "[ERROR] No healthy instances found." >&2
  exit 1
fi

echo "Target instances: ${IDS[*]}"

# Ensure each instance is SSM managed (wait up to 90s if needed)
SSM_OK=()
for iid in "${IDS[@]}"; do
  echo -n "Checking SSM registration for $iid ... "
  for attempt in {1..9}; do
    if aws ssm describe-instance-information --query 'InstanceInformationList[].InstanceId' --output text --region "$REGION" | tr '\t' '\n' | grep -q "^$iid$"; then
      echo "OK"; SSM_OK+=("$iid"); break
    fi
    echo -n "."; sleep 10
  done
  echo
done

if [[ ${#SSM_OK[@]} -ne ${#IDS[@]} ]]; then
  echo "[ERROR] Some instances are not registered in SSM yet. Aborting." >&2
  exit 1
fi

echo "Injecting public key via a single send-command (JSON parameters)..."

# Build JSON safely (no need to escape spaces since we inject via variable expansion outside single quotes)
PUB_KEY_JSON_SAFE="$PUB_KEY_CONTENT"
PARAM_JSON=$(cat <<JSON
{"commands":[
"mkdir -p /home/ec2-user/.ssh",
"touch /home/ec2-user/.ssh/authorized_keys",
"grep -qxF '$PUB_KEY_JSON_SAFE' /home/ec2-user/.ssh/authorized_keys || echo '$PUB_KEY_JSON_SAFE' >> /home/ec2-user/.ssh/authorized_keys",
"chown -R ec2-user:ec2-user /home/ec2-user/.ssh",
"chmod 700 /home/ec2-user/.ssh",
"chmod 600 /home/ec2-user/.ssh/authorized_keys"
]}
JSON
)

aws ssm send-command \
  --instance-ids "${IDS[@]}" \
  --document-name AWS-RunShellScript \
  --comment "Add new SSH public key" \
  --parameters "$PARAM_JSON" \
  --region "$REGION" \
  --query 'Command.CommandId' --output text

echo "Waiting 5s before verification..."; sleep 5

# Verification per instance
for iid in "${IDS[@]}"; do
  VERIFY_JSON=$(cat <<JSON
{"commands":["grep -c '${PUB_KEY_CONTENT%% *}' /home/ec2-user/.ssh/authorized_keys || true"]}
JSON
)
  CMD_ID=$(aws ssm send-command \
    --instance-ids "$iid" \
    --document-name AWS-RunShellScript \
    --comment "Verify SSH key presence" \
    --parameters "$VERIFY_JSON" \
    --region "$REGION" \
    --query 'Command.CommandId' --output text)
  sleep 2
  COUNT=$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$iid" --region "$REGION" --query 'StandardOutputContent' --output text || echo "0")
  echo "Instance $iid occurrences: $COUNT"
  if [[ "$COUNT" == "0" ]]; then
    echo "[WARN] Key not found on $iid" >&2
  fi
done

echo "Done. You can now SSH (ensure you have the matching private key)."
