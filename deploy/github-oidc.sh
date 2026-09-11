#!/usr/bin/env bash
# Let GitHub Actions deploy without a stored AWS key.
#
# GitHub signs a short-lived OIDC token for each workflow run; AWS trusts the
# issuer and exchanges that token for temporary credentials. Nothing long-lived
# exists to leak, and revoking access is deleting one role.
#
# The trust policy is the security boundary and it is deliberately narrow. This
# repository is public, so anyone can open a pull request against it. The `sub`
# condition pins the role to pushes on main of this one repository — a fork, a
# pull request, a tag or another branch produces a different `sub` and cannot
# assume it. Widen that condition and a stranger's pull request gains push access
# to production.
#
# Needs `gh` authenticated: the trust policy pins the numeric owner and repository
# ids, which the GitHub API supplies.
#
#   AWS_PROFILE=xrv bash deploy/github-oidc.sh
#
# Idempotent. Prints the role ARN to store as the AWS_DEPLOY_ROLE_ARN secret.

set -euo pipefail

REGION="${AWS_REGION:-eu-central-1}"
NAME="${XRV_NAME:-xrechnung-validator}"
REPO="${XRV_REPO:-harshal0001/xrechnung-validator}"
BRANCH="${XRV_DEPLOY_BRANCH:-main}"
ROLE="${NAME}-deploy"
HOST="token.actions.githubusercontent.com"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
PROVIDER="arn:aws:iam::${ACCOUNT}:oidc-provider/${HOST}"

# ---- the identity provider ---------------------------------------------------
# No thumbprint is passed: IAM has trusted GitHub's CA since 2023 and a pinned
# thumbprint is one more thing to rotate when it expires.
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER" >/dev/null 2>&1; then
  echo "  provider   exists"
else
  aws iam create-open-id-connect-provider --url "https://${HOST}" \
    --client-id-list sts.amazonaws.com >/dev/null
  echo "  provider   created"
fi

# ---- the role ----------------------------------------------------------------
# Two accepted subjects, both exact, both pinned to one branch.
#
# GitHub is migrating to an *immutable* subject claim that carries the numeric
# owner and repository ids — `repo:owner@86665758/name@1357239540:ref:...` rather
# than `repo:owner/name:ref:...`. Which form a repository gets is GitHub's call
# and can change under you; a policy written for the documented form alone fails
# with a bare "Not authorized to perform sts:AssumeRoleWithWebIdentity" that says
# nothing about why. CloudTrail's `userIdentity.userName` is where the subject
# actually sent is visible.
#
# The id-bearing form is the stronger of the two: ids survive a rename and cannot
# be claimed by someone who registers the name after you delete the repository.
OWNER_ID=$(gh api "repos/${REPO}" --jq .owner.id)
REPO_ID=$(gh api "repos/${REPO}" --jq .id)
OWNER=${REPO%%/*}
NAME_ONLY=${REPO##*/}
SUB_IMMUTABLE="repo:${OWNER}@${OWNER_ID}/${NAME_ONLY}@${REPO_ID}:ref:refs/heads/${BRANCH}"
SUB_LEGACY="repo:${REPO}:ref:refs/heads/${BRANCH}"

TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{
  "Effect":"Allow",
  "Principal":{"Federated":"${PROVIDER}"},
  "Action":"sts:AssumeRoleWithWebIdentity",
  "Condition":{
    "StringEquals":{
      "${HOST}:aud":"sts.amazonaws.com",
      "${HOST}:sub":["${SUB_IMMUTABLE}","${SUB_LEGACY}"]
    }}}]}
JSON
)
echo "  subject    ${SUB_IMMUTABLE}"
echo "             ${SUB_LEGACY}"

if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST"
  echo "  role       $ROLE (trust refreshed)"
else
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" \
    --description "GitHub Actions deploys ${NAME} from ${REPO}@${BRANCH}" >/dev/null
  aws iam wait role-exists --role-name "$ROLE"
  echo "  role       $ROLE (created)"
fi

# ---- what it may do ----------------------------------------------------------
# Push an image to one repository and point one function at it. It cannot create
# functions, read logs, touch IAM, or reach any other resource in the account.
POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"EcrLogin","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Sid":"PushImage","Effect":"Allow",
  "Action":["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload",
            "ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage",
            "ecr:BatchGetImage","ecr:DescribeImages"],
  "Resource":"arn:aws:ecr:${REGION}:${ACCOUNT}:repository/${NAME}"},
 {"Sid":"UpdateFunction","Effect":"Allow",
  "Action":["lambda:UpdateFunctionCode","lambda:GetFunction","lambda:GetFunctionConfiguration"],
  "Resource":"arn:aws:lambda:${REGION}:${ACCOUNT}:function:${NAME}"}]}
JSON
)
aws iam put-role-policy --role-name "$ROLE" --policy-name deploy --policy-document "$POLICY"
echo "  policy     attached (ecr push + lambda update, nothing else)"

ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)
echo
echo "  Store this as the AWS_DEPLOY_ROLE_ARN repository secret — it contains the"
echo "  account id, and this repository is public:"
echo
echo "    gh secret set AWS_DEPLOY_ROLE_ARN --body '$ARN'"
