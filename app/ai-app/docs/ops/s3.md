# Managed Infra. S3

This page documents how KDCube services access the `S3` bucket using the **EC2 instance role** (no static keys) and how to set up **local development** access.

---

## 1) EC2 config

### 1.1 Identify the instance + role (run on the EC2 box)

```bash
# Get IMDSv2 token
TOKEN=$(curl -sS -X PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

# Instance ID
INSTANCE_ID=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-id)
echo "INSTANCE_ID=$INSTANCE_ID"   # e.g. <INSTANCE_ID>

# Region
REGION=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/placement/region)
echo "REGION=$REGION"             # e.g. <REGION>

# EC2 role name (instance profile role)
ROLE_NAME=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/iam/security-credentials/)
echo "ROLE_NAME=$ROLE_NAME"       # e.g. <ROLE_NAME>
```

### 1.2 Ensure containers can use the instance role (IMDSv2 hop limit)

We need IMDSv2 **HttpPutResponseHopLimit = 2** on `<INSTANCE_ID>`.

**Option A (preferred):** Flip it in Console
EC2 → **Instances** → select `<INSTANCE_ID>` → **Actions** → *Instance settings* → **Modify instance metadata options**

* Metadata accessible: **Enabled**
* Metadata version: **V2 (required)**
* **Hop limit: 2** → **Save**

**Option B (CLI):** If you have permission

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id <INSTANCE_ID> --region <REGION> \
  --http-tokens required --http-put-response-hop-limit 2
```

**Verify**

```bash
aws ec2 describe-instances --instance-ids <INSTANCE_ID> --region <REGION> \
  --query 'Reservations[0].Instances[0].MetadataOptions'
# Expect "HttpTokens": "required", "HttpPutResponseHopLimit": 2
```

### 1.3 Grant the EC2 role S3 permissions (least privilege)

Attach to role `<ROLE_NAME>` (or create a dedicated role and attach it to `<INSTANCE_ID>`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "BucketLevel",
      "Effect": "Allow",
      "Action": ["s3:ListBucket","s3:GetBucketLocation","s3:ListBucketMultipartUploads","s3:ListBucketVersions"],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>"
    },
    { "Sid": "ObjectLevel",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject","s3:PutObject","s3:DeleteObject",
        "s3:GetObjectTagging","s3:PutObjectTagging",
        "s3:AbortMultipartUpload","s3:GetObjectVersion","s3:DeleteObjectVersion"
      ],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/*"
    }
  ]
}
```

If the bucket uses **SSE-KMS**, also allow the role to use the key and update the key policy:

**IAM permission on role:**

```json
{
  "Effect": "Allow",
  "Action": ["kms:Encrypt","kms:Decrypt","kms:GenerateDataKey","kms:DescribeKey"],
  "Resource": "arn:aws:kms:<REGION>:<ACCOUNT_ID>:key/<KEY_ID_OR_ALIAS_ARN>"
}
```

**KMS key policy addition:**

```json
{
  "Sid": "AllowRoleUseOfKey",
  "Effect": "Allow",
  "Principal": { "AWS": "arn:aws:iam::<ACCOUNT_ID>:role/<ROLE_NAME>" },
  "Action": ["kms:Encrypt","kms:Decrypt","kms:GenerateDataKey","kms:DescribeKey"],
  "Resource": "*"
}
```

### 1.4 Quick validation (from EC2 host)

```bash
REGION=<REGION>
BUCKET=<BUCKET_NAME>
KEY="probe-$(date +%s).txt"

aws --region "$REGION" s3 ls "s3://$BUCKET/" --max-items 1
printf 'hello\n' | aws --region "$REGION" s3 cp - "s3://$BUCKET/$KEY"
aws --region "$REGION" s3api head-object --bucket "$BUCKET" --key "$KEY"
aws --region "$REGION" s3 cp "s3://$BUCKET/$KEY" /tmp/probe.txt
aws --region "$REGION" s3 rm "s3://$BUCKET/$KEY"
```

---

## 2) Local dev env – role/profile config

> Preferred: SSO. If SSO isn’t available, DevOps can provision a **programmatic-only IAM user** (e.g. `chatbot-dev-<profile-name>`) scoped to the bucket.

### 2.1 Minimal IAM policy for local dev user (full bucket access)

Attach to the IAM user (change placeholders):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "BucketLevel",
      "Effect": "Allow",
      "Action": ["s3:ListBucket","s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>"
    },
    { "Sid": "ObjectLevel",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject","s3:PutObject","s3:DeleteObject",
        "s3:AbortMultipartUpload","s3:ListBucketMultipartUploads","s3:ListMultipartUploadParts"
      ],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/*"
    }
  ]
}
```

If using **SSE-KMS**:

```json
{
  "Effect": "Allow",
  "Action": ["kms:Encrypt","kms:Decrypt","kms:GenerateDataKey*","kms:DescribeKey"],
  "Resource": "arn:aws:kms:<REGION>:<ACCOUNT_ID>:key/<KEY_ID>"
}
```

> **Security note:** Do **not** put keys in a README or repo. Share via a secret channel or use a password manager. Consider `aws-vault` for local use.

### 2.2 Configure your local AWS CLI profile

```bash
aws configure --profile chat-dev-<profile-name>
# paste Access key ID / Secret, set default region (<REGION>), output=json
```

Or edit files:

**~/.aws/credentials**

```
[chat-dev-<profile-name>]
aws_access_key_id = <ACCESS_KEY_ID>
aws_secret_access_key = <SECRET_ACCESS_KEY>
```

**~/.aws/config**

```
[profile chat-dev-<profile-name>]
region = <REGION>
output = json
```

**Test**

```bash
AWS_PROFILE=chat-dev-<profile-name> aws sts get-caller-identity
AWS_PROFILE=chat-dev-<profile-name> aws s3 ls s3://<BUCKET_NAME>/
```

### 2.3 Using the profile from code (boto3)

```python
import boto3
session = boto3.Session(profile_name="chat-dev-<profile-name>", region_name="<REGION>")
s3 = session.client("s3")
s3.put_object(Bucket="<BUCKET_NAME>", Key="dev/probe.txt", Body=b"hi")
```

---

## 3) Docker Compose on EC2 (use host identity — no keys)

### 3.1 Environment (.env.backend)

```
AWS_REGION=<REGION>
AWS_DEFAULT_REGION=<REGION>
AWS_EC2_METADATA_DISABLED=false
# Don’t proxy IMDS
NO_PROXY=169.254.169.254,localhost,127.0.0.1
# App storage URI (example)
KB_STORAGE_URI=s3://<BUCKET_NAME>/kb/
```

### 3.2 Services that touch S3 (e.g., kb, chat, dramatiq)

Ensure they load `.env.backend` (they already do), nothing else required. **Do not** set `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` in env for EC2.

Example (snippet):

```yaml
environment:
  - AWS_REGION=${AWS_REGION}
  - AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION}
  - AWS_EC2_METADATA_DISABLED=${AWS_EC2_METADATA_DISABLED}
  - NO_PROXY=${NO_PROXY}
  - KB_STORAGE_URI=${KB_STORAGE_URI}
```

> With **hop limit = 2**, containers on the default bridge network will fetch short-lived creds from IMDSv2 automatically via the EC2 role `<ROLE_NAME>`.

### 3.3 Container-side validation (one-off)

```bash
BUCKET=<BUCKET_NAME>
REGION=<REGION>
PREFIX=test/$(hostname)/
KEY="${PREFIX}probe-$(date +%s).txt"

# prove metadata reachable from a container
docker run --rm curlimages/curl:8.10.1 sh -lc '
  T=$(curl -sS -X PUT http://169.254.169.254/latest/api/token \
     -H "X-aws-ec2-metadata-token-ttl-seconds: 21600); \
  curl -sS -H "X-aws-ec2-metadata-token: $T" \
     http://169.254.169.254/latest/meta-data/iam/info'

# S3 ops
printf 'hello from container\n' | docker run --rm -i \
  -e AWS_REGION="$REGION" -e NO_PROXY=169.254.169.254,localhost,127.0.0.1 \
  amazon/aws-cli s3 cp - "s3://$BUCKET/$KEY"

docker run --rm -e AWS_REGION="$REGION" \
  -e NO_PROXY=169.254.169.254,localhost,127.0.0.1 \
  amazon/aws-cli s3api head-object --bucket "$BUCKET" --key "$KEY"

docker run --rm -e AWS_REGION="$REGION" \
  -e NO_PROXY=169.254.169.254,localhost,127.0.0.1 \
  amazon/aws-cli s3 cp "s3://$BUCKET/$KEY" -

docker run --rm -e AWS_REGION="$REGION" \
  -e NO_PROXY=169.254.169.254,localhost,127.0.0.1 \
  amazon/aws-cli s3 rm "s3://$BUCKET/$KEY"
```

---

### Troubleshooting quick list

* **`UnauthorizedOperation`** on modify metadata → your IAM principal lacks `ec2:ModifyInstanceMetadataOptions`; ask DevOps or use console.
* **`AccessDenied`** on S3 → role `<ROLE_NAME>` lacks bucket or KMS perms; apply policies above.
* **Timeout/IMDS failure** in containers → ensure hop limit = 2 and `NO_PROXY` includes `169.254.169.254`.
* **Cert errors** in slim images → install `ca-certificates` in your base image.

---

**Placeholders to replace**

* `<INSTANCE_ID>` — e.g., `i-...`
* `<REGION>` — e.g., `eu-west-1`
* `<ROLE_NAME>` — instance assumed identity role
* `<ACCOUNT_ID>`
* `<BUCKET_NAME>`
* `<KEY_ID_OR_ALIAS_ARN>` / `<KEY_ID>` — your KMS key if bucket uses SSE-KMS
* `<profile-name>` — local dev profile unique name
