# Deployment

The service ships as one container image that runs unmodified on AWS Lambda, on
Cloud Run, on Render, or on a laptop. Nothing in the application knows where it
is: there is no Lambda handler, no platform branch, and no second Dockerfile.
The Lambda Web Adapter in the image is an extension the Lambda runtime loads from
`/opt/extensions`; every other host never reads the file.

AWS is the deployment that exists. The others would work; they are not set up.

## Deploy to AWS

You authenticate; the script never handles credentials.

```bash
aws configure sso        # or: aws configure
bash deploy/aws.sh
```

It creates one ECR repository, one IAM role, one Lambda function and one Function
URL, and it is safe to re-run — every step updates rather than fails if the thing
already exists.

| Setting | Value | Why |
|---|---|---|
| Region | `eu-central-1` | Frankfurt. German invoices stay in Germany |
| Architecture | `arm64` | Graviton, ~20% cheaper. CI builds arm64 on every push to main |
| Memory | 1024 MB | Measured peak is ~220 MB; this is headroom, not a guess |
| Timeout | 30 s | 27 ms per document warm |
| Front door | Function URL | Free, one call. API Gateway would add cost and routing this service does not need |
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

## New accounts cannot serve public traffic

A newly created AWS account is restricted until AWS finishes vetting it, and one of
the things held back is serving **public, unauthenticated** endpoints. The symptom is
a function URL that returns 403 while everything about it is correct.

`deploy/status.sh` reports the tell: `ConcurrentExecutions` is 10 on a restricted
account and 1000 on a normal one. The two lift together.

What the restriction does and does not cover, established by testing each path:

| Path | Restricted account |
|---|---|
| `aws lambda invoke` | works |
| Function URL, SigV4-signed | works |
| Function URL, `AuthType: NONE` | **403** |
| CloudFront in front of it | **403** — CloudFront is public traffic too |

So CloudFront does **not** work around it, which is worth knowing before reaching for
it. Nothing needs changing; the configuration is already correct and starts working
when the account matures.

## Measure the cold start

The README's results table has an empty "cold start to first response" row. It
stays empty until measured — never estimated.

```bash
bash deploy/measure.sh https://xxxx.lambda-url.eu-central-1.on.aws/
```

It forces a cold start by updating the function configuration, which discards
warm execution environments, then times the first request against the next five.

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
aws lambda delete-function-url-config --function-name xrechnung-validator --region eu-central-1
aws lambda delete-function          --function-name xrechnung-validator --region eu-central-1
aws ecr    delete-repository        --repository-name xrechnung-validator --region eu-central-1 --force
aws iam    detach-role-policy --role-name xrechnung-validator-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam    delete-role --role-name xrechnung-validator-role
```
