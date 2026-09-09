#!/usr/bin/env bash
# Deploy the validator to AWS Lambda as a container image behind a Function URL.
#
# Idempotent on purpose: every step checks whether the thing already exists and
# updates rather than fails. A deploy script that only works on an empty account
# is a script you cannot use twice, and the second run is the one you make under
# pressure.
#
#   aws configure sso            # or: aws configure   — you, not this script
#   bash deploy/aws.sh
#
# It never creates credentials and never prints them. It creates: one ECR
# repository, one IAM role, one Lambda function, one Function URL.

set -euo pipefail

REGION="${AWS_REGION:-eu-central-1}"          # Frankfurt: German data stays in Germany
NAME="${XRV_NAME:-xrechnung-validator}"
MEMORY="${XRV_MEMORY:-1024}"                  # measured peak ~220 MB; 1 GiB is headroom
TIMEOUT="${XRV_TIMEOUT:-30}"                  # 27 ms/document warm; 30 s is generous
ARCH="${XRV_ARCH:-arm64}"                     # Graviton: ~20% cheaper, proven in CI
ROLE_NAME="${NAME}-role"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

command -v aws >/dev/null || { echo "aws CLI not found"; exit 1; }
command -v docker >/dev/null || { echo "docker not found"; exit 1; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text) || {
  echo "Not authenticated. Run 'aws configure sso' or 'aws configure' first."; exit 1; }
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${NAME}:latest"

say "Account ${ACCOUNT}, region ${REGION}, ${ARCH}, ${MEMORY} MB"

# ---- 1. ECR repository -------------------------------------------------------
if ! aws ecr describe-repositories --repository-names "$NAME" --region "$REGION" >/dev/null 2>&1; then
  say "Creating ECR repository ${NAME}"
  aws ecr create-repository --repository-name "$NAME" --region "$REGION" \
    --image-scanning-configuration scanOnPush=true >/dev/null
else
  say "ECR repository ${NAME} already exists"
fi

# ---- 2. Build and push -------------------------------------------------------
# Built for the target architecture explicitly. Building arm64 on an x86 laptop
# goes through emulation and is slow, but it is the same image CI already builds
# for arm64 on every push to main, so it is a proven path rather than a guess.
# --provenance/--sbom off, and the docker media type forced. buildx defaults to
# attaching attestations, which makes the push an OCI manifest list; Lambda
# accepts only a Docker v2 manifest and rejects the image at CreateFunction with
# "media type ... is not supported". The build succeeds and the push succeeds —
# it fails one step later, which is why this is worth a comment.
say "Building for linux/${ARCH}"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform "linux/${ARCH}" \
  --provenance=false --sbom=false \
  --output "type=image,name=${IMAGE},push=true,oci-mediatypes=false" .

# ---- 3. Execution role -------------------------------------------------------
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  say "Creating execution role ${ROLE_NAME}"
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' >/dev/null
  # Logs only. The function reads no AWS resources: the rule set is baked into
  # the image and there is no database, so it needs no other permission.
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "Waiting for the role to propagate..."
  sleep 12
else
  say "Execution role ${ROLE_NAME} already exists"
fi
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

# ---- 4. Function -------------------------------------------------------------
if aws lambda get-function --function-name "$NAME" --region "$REGION" >/dev/null 2>&1; then
  say "Updating function ${NAME}"
  aws lambda update-function-code --function-name "$NAME" --region "$REGION" \
    --image-uri "$IMAGE" >/dev/null
  aws lambda wait function-updated --function-name "$NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$NAME" --region "$REGION" \
    --memory-size "$MEMORY" --timeout "$TIMEOUT" >/dev/null
else
  say "Creating function ${NAME}"
  aws lambda create-function --function-name "$NAME" --region "$REGION" \
    --package-type Image --code "ImageUri=${IMAGE}" --role "$ROLE_ARN" \
    --architectures "$ARCH" --memory-size "$MEMORY" --timeout "$TIMEOUT" >/dev/null
fi
aws lambda wait function-active-v2 --function-name "$NAME" --region "$REGION"

# ---- 5. Function URL ---------------------------------------------------------
# A Function URL rather than API Gateway: it is free, it is one call, and this
# service has one public endpoint. API Gateway would add cost and a second thing
# to configure for routing we do not need.
if ! aws lambda get-function-url-config --function-name "$NAME" --region "$REGION" >/dev/null 2>&1; then
  say "Creating Function URL"
  aws lambda create-function-url-config --function-name "$NAME" --region "$REGION" \
    --auth-type NONE >/dev/null
  aws lambda add-permission --function-name "$NAME" --region "$REGION" \
    --statement-id FunctionURLAllowPublicAccess --action lambda:InvokeFunctionUrl \
    --principal '*' --function-url-auth-type NONE >/dev/null
fi
URL=$(aws lambda get-function-url-config --function-name "$NAME" --region "$REGION" \
      --query FunctionUrl --output text)

say "Deployed"
echo "  $URL"
echo
echo "Measure the cold start with deploy/measure.sh, then put the number in README.md."
