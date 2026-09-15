#!/usr/bin/env bash
# Put a real hostname on the HTTP API.
#
# The execute-api hostname works, but it is bound to one API Gateway resource and
# reads as temporary. A link in a pinned post or a CV has to outlive the resource
# behind it, so the public name is a subdomain you own, mapped onto the API.
# Nothing downstream changes: the API, the function and the execute-api URL all
# keep working, and the keep-warm ping can keep using them.
#
# Three AWS pieces, in order:
#   1. an ACM certificate for the subdomain, validated by a DNS record you add
#   2. an API Gateway custom domain name that presents that certificate
#   3. an API mapping from that domain onto the existing API's $default stage
# and two DNS records at the registrar, which this script prints and waits for.
#
# Safe to re-run: every step adopts what already exists. Run it once to get the
# validation record, add the record, run it again — or leave it running; it waits.
#
#   AWS_PROFILE=xrv bash deploy/domain.sh
#   XRV_DOMAIN=other.example.dev AWS_PROFILE=xrv bash deploy/domain.sh

set -euo pipefail

REGION="${AWS_REGION:-eu-central-1}"
NAME="${XRV_NAME:-xrechnung-validator}"
API_NAME="${XRV_API_NAME:-$NAME}"
DOMAIN="${XRV_DOMAIN:-xrechnung.harshalkothari.tech}"
AWS="aws --region $REGION"

# "xrechnung" and "harshalkothari.tech": registrar panels want the host part alone.
SUB="${DOMAIN%%.*}"
ROOT="${DOMAIN#*.}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# Registrar UIs take the host without the zone and usually reject a trailing dot.
host_part() { local n="${1%.}"; echo "${n%.${ROOT}}"; }

command -v aws >/dev/null || { echo "aws CLI not found"; exit 1; }
$AWS sts get-caller-identity >/dev/null || {
  echo "Not authenticated. Run 'aws configure sso' or 'aws configure' first."; exit 1; }

API=$($AWS apigatewayv2 get-apis --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text)
if [ "$API" = "None" ] || [ -z "$API" ]; then
  echo "No HTTP API named ${API_NAME}. Run deploy/apigateway.sh first."; exit 1
fi
echo "  domain     $DOMAIN"
echo "  api        $API"

# ---- 1. Certificate ----------------------------------------------------------
# Regional custom domains need the certificate in the API's own region — not
# us-east-1, which is only for CloudFront. ACM certificates are free.
CERT=$($AWS acm list-certificates \
  --query "CertificateSummaryList[?DomainName=='${DOMAIN}'].CertificateArn | [0]" --output text)
if [ "$CERT" = "None" ] || [ -z "$CERT" ]; then
  say "Requesting certificate for ${DOMAIN}"
  CERT=$($AWS acm request-certificate --domain-name "$DOMAIN" --validation-method DNS \
           --query CertificateArn --output text)
else
  say "Certificate already requested"
fi
echo "  $CERT"

STATUS=$($AWS acm describe-certificate --certificate-arn "$CERT" --query Certificate.Status --output text)
if [ "$STATUS" != "ISSUED" ]; then
  # The validation record appears a few seconds after the request.
  for _ in $(seq 1 20); do
    V_NAME=$($AWS acm describe-certificate --certificate-arn "$CERT" \
      --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Name' --output text)
    [ "$V_NAME" != "None" ] && [ -n "$V_NAME" ] && break
    sleep 3
  done
  V_VALUE=$($AWS acm describe-certificate --certificate-arn "$CERT" \
    --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Value' --output text)

  say "DNS record 1 of 2 — certificate validation. Add this CNAME at the registrar:"
  echo "  type     CNAME"
  echo "  host     $(host_part "$V_NAME")"
  echo "  value    ${V_VALUE%.}"
  echo
  echo "  (full name: ${V_NAME})"
  echo
  echo "Waiting for ACM to see it. This can take 5–30 minutes after the record is added;"
  echo "leave this running, or Ctrl-C and re-run later — nothing is lost."
  $AWS acm wait certificate-validated --certificate-arn "$CERT"
fi
echo "  certificate ISSUED"

# ---- 2. Custom domain name ---------------------------------------------------
if $AWS apigatewayv2 get-domain-name --domain-name "$DOMAIN" >/dev/null 2>&1; then
  say "Custom domain ${DOMAIN} already exists"
else
  say "Creating custom domain ${DOMAIN}"
  $AWS apigatewayv2 create-domain-name --domain-name "$DOMAIN" \
    --domain-name-configurations "CertificateArn=${CERT},EndpointType=REGIONAL,SecurityPolicy=TLS_1_2" >/dev/null
fi
TARGET=$($AWS apigatewayv2 get-domain-name --domain-name "$DOMAIN" \
  --query 'DomainNameConfigurations[0].ApiGatewayDomainName' --output text)
echo "  target     $TARGET"

# ---- 3. Mapping onto the API ----------------------------------------------------
# One mapping, empty base path, onto $default — so https://DOMAIN/ is the app root
# exactly as the execute-api URL is. No path prefix to strip anywhere.
if $AWS apigatewayv2 get-api-mappings --domain-name "$DOMAIN" --query 'Items[].ApiId' --output text \
     | grep -qw "$API"; then
  echo "  mapping    already present"
else
  $AWS apigatewayv2 create-api-mapping --domain-name "$DOMAIN" --api-id "$API" --stage '$default' >/dev/null
  echo "  mapping    created"
fi

# ---- 4. The record that makes it public --------------------------------------
say "DNS record 2 of 2 — the hostname itself. Add this CNAME at the registrar:"
echo "  type     CNAME"
echo "  host     $SUB"
echo "  value    $TARGET"
echo
echo "Waiting for https://${DOMAIN}/healthz to answer (DNS propagation, usually minutes)."
for i in $(seq 1 60); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${DOMAIN}/healthz" || true)
  if [ "$CODE" = "200" ]; then
    say "Live"
    echo "  https://${DOMAIN}"
    echo
    echo "Now replace the execute-api URL where it is published:"
    echo "  grep -rn 'execute-api' README.md frontend/index.html deploy/"
    exit 0
  fi
  [ $((i % 6)) -eq 0 ] && echo "  still waiting (${i} × 10 s, last HTTP ${CODE:-none})"
  sleep 10
done

echo "  ${DOMAIN} did not answer within 10 minutes."
echo "  If the CNAME is in place, DNS is still propagating — re-run to keep waiting."
exit 1
