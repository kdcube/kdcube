---
id: repo:kdcube-ai-app/app/ai-app/docs/service/ecs/iam-README.md
title: "IAM"
summary: "Minimum IAM permissions for ECS tasks and CI/CD."
tags: ["service", "ecs", "iam", "security"]
keywords: ["task execution role", "ECR", "CloudWatch logs", "IAM policy"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/ecs/custom-ecs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/ecs/metrics-autoscaling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
---
# ECS IAM Guide

This document lists **minimum IAM permissions** for ECS tasks and CI/CD.

---

## 1) Task Execution Role (ECS)

Used by ECS to pull images and write logs.

Required AWS‑managed policy:
- `AmazonECSTaskExecutionRolePolicy`

This covers:
- ECR image pull
- CloudWatch Logs

---

## 2) Task Role (Application)

Attach per‑service permissions here.

### Common for all services
- Read secrets from Secrets Manager or SSM
- Read configs from S3 (if you store envs/configs there)

Example (SSM + Secrets Manager):
```json
{
  "Effect": "Allow",
  "Action": [
    "ssm:GetParameter",
    "ssm:GetParameters",
    "secretsmanager:GetSecretValue"
  ],
  "Resource": "*"
}
```

### Ingress + Processor
- Access to storage (S3 or filesystem via EFS)
- Optional: KMS decrypt if S3 uses SSE‑KMS

Example (S3 read/write):
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:GetObject",
    "s3:PutObject",
    "s3:ListBucket"
  ],
  "Resource": [
    "arn:aws:s3:::<bucket>",
    "arn:aws:s3:::<bucket>/*"
  ]
}
```

### Metrics service
If exporting to CloudWatch:
```json
{
  "Effect": "Allow",
  "Action": "cloudwatch:PutMetricData",
  "Resource": "*"
}
```

---

## 3) CI/CD (ECR Push)

CI role or user needs:
```json
{
  "Effect": "Allow",
  "Action": [
    "ecr:BatchCheckLayerAvailability",
    "ecr:CompleteLayerUpload",
    "ecr:GetAuthorizationToken",
    "ecr:InitiateLayerUpload",
    "ecr:PutImage",
    "ecr:UploadLayerPart"
  ],
  "Resource": "*"
}
```

---

## 4) CloudWatch Logs

If you create log groups from CI or IaC:
```json
{
  "Effect": "Allow",
  "Action": [
    "logs:CreateLogGroup",
    "logs:CreateLogStream",
    "logs:PutLogEvents"
  ],
  "Resource": "*"
}
```
