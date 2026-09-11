# Deployment

The service ships as one container image that runs unmodified on AWS Lambda, on
Cloud Run, on Render, or on a laptop. Nothing in the application knows where it
is: there is no Lambda handler, no platform branch, and no second Dockerfile.
The Lambda Web Adapter in the image is an extension the Lambda runtime loads from
`/opt/extensions`; every other host never reads the file.

AWS is the deployment that exists. The others would work; they are not set up.

## Deploying

**Merging to main deploys.** The `deploy` job in CI runs only after the tests,
the frontend and the image build have passed, pushes an arm64 image tagged with
the commit sha, points the function at it, and then proves the live endpoint
answers — a deploy that reports success while the service is down is worse than
a failed one, because nobody looks again.

No AWS key is stored anywhere. GitHub signs a short-lived OIDC token per run and
AWS exchanges it for temporary credentials. The role is created by
`deploy/github-oidc.sh`, may push one image and update one function, and trusts
only pushes to main of this repository — this repo is public, so a trust policy
any wider would hand push-to-production access to a stranger's pull request.

```bash
bash deploy/github-oidc.sh          # once: identity provider, role, policy
gh secret set AWS_DEPLOY_ROLE_ARN --body '<the ARN it prints>'
```

The image is tagged by commit sha rather than `:latest`, so the function records
which commit is live and a rollback is one call with an older sha. ECR keeps the
live image and one previous: each is ~214 MB against a 500 MB free tier, so a
third would start costing money.

## First deploy, or deploying by hand

`deploy/aws.sh` creates everything from nothing and is what the CI job would
have to repeat otherwise. It needs Docker locally. You authenticate; the script
never handles credentials.

```bash
aws configure sso        # or: aws configure
bash deploy/aws.sh          # image, role, function, Function URL
bash deploy/apigateway.sh   # the public front door
bash deploy/status.sh       # what is live, and what is not
```

The first creates one ECR repository, one IAM role, one Lambda function and one
Function URL. The second puts an HTTP API in front of it, which is the door that
actually answers on this account — see below. Both are safe to re-run: every step
updates, or adopts what exists, rather than failing.

| Setting | Value | Why |
|---|---|---|
| Region | `eu-central-1` | Frankfurt. German invoices stay in Germany |
| Architecture | `arm64` | Graviton, ~20% cheaper. CI builds arm64 on every push to main |
| Memory | 1024 MB | Measured peak is ~220 MB; this is headroom, not a guess |
| Timeout | 30 s | 27 ms per document warm |
| Front door | HTTP API | A Function URL is free and would be enough, but returns 403 on this account. See below |
| IAM | logs only | The rule set is baked into the image and there is no database, so the function reads no AWS resource |

### Why Lambda suits this workload

Its **init phase is unbilled and runs at full vCPU**. Startup compiles three XSD
schemas and four Schematron stylesheets — 2.4 s measured — and on Lambda that is
free. On a platform that bills startup it is not.

One worker per instance, deliberately: SaxonC-HE is not thread-safe, so
validation is serialised inside the process and concurrency comes from more
instances. That is how Lambda scales anyway.

## One thing that will bite you

`docker buildx --push` attaches provenance and SBOM attestations by default, which
makes the push an OCI manifest list. **Lambda accepts only a Docker v2 manifest** and
rejects the image at `CreateFunction` with *"media type ... is not supported"* — after
the build and the push have both succeeded. The script passes `--provenance=false
--sbom=false` and forces the Docker media type for exactly this reason.

## The Function URL returns 403, and an HTTP API does not

A Lambda Function URL with `AuthType: NONE` is the cheapest front door there is:
no extra service, one API call, no per-request charge. On this account it returns
403 to everyone, including a plain `curl` of `/healthz`, while the function itself
is healthy.

It is not a misconfiguration. Each path was tested against the same unchanged
function:

| Path | Result |
|---|---|
| `aws lambda invoke` | 200 |
| Function URL, SigV4-signed | 200 |
| Function URL, `AuthType: NONE` | **403** |
| CloudFront with Origin Access Control in front of the Function URL | **403** |
| **API Gateway HTTP API in front of the function** | **200** |

The last row is the one that matters, and it corrects an obvious reading of the
first four. A signed request working while an unsigned one fails looks like a
block on public unauthenticated traffic, and CloudFront returning 403 too looks
like confirmation. It is not: the HTTP API serves exactly that traffic, to the
same function, publicly and unauthenticated. What is restricted is the Function
URL, not the audience — so CloudFront cannot work around it, because CloudFront
reaches the function *through* the Function URL, and API Gateway does not.

A newly created account is also capped at 10 concurrent executions against a
normal 1000. `deploy/status.sh` reports that figure as a proxy for account
maturity; expect the Function URL to start answering when it rises.

## Measure the cold start

The README's results table has an empty "cold start to first response" row. It
stays empty until measured — never estimated.

```bash
bash deploy/measure.sh https://xxxx.execute-api.eu-central-1.amazonaws.com/
```

It forces a cold start by updating the function configuration, which discards
warm execution environments, then times the first request against the next five.
Measured here: 3.6–4.7 s to first response over three runs. Lambda does not bill
the init phase, so the schema and stylesheet compilation inside that figure is free.

## Running it anywhere else

The same image, no changes:

```bash
gcloud run deploy xrechnung-validator --source . \
  --region europe-west3 --memory 1Gi --cpu 1 \
  --min-instances 0 --max-instances 3 --concurrency 4 --allow-unauthenticated
```

`--concurrency 4`, not the higher default: with Saxon serialised behind a lock,
extra concurrent requests queue inside one instance instead of running. Better to
let the platform start another instance.

## Teardown

```bash
aws apigatewayv2 delete-api --api-id "$(aws apigatewayv2 get-apis --region eu-central-1 \
  --query "Items[?Name=='xrechnung-validator'].ApiId | [0]" --output text)" --region eu-central-1
aws lambda delete-function-url-config --function-name xrechnung-validator --region eu-central-1
aws lambda delete-function          --function-name xrechnung-validator --region eu-central-1
aws ecr    delete-repository        --repository-name xrechnung-validator --region eu-central-1 --force
aws iam    detach-role-policy --role-name xrechnung-validator-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam    delete-role --role-name xrechnung-validator-role
```
