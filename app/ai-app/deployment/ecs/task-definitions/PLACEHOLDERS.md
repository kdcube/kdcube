# =============================================================================
# Placeholder reference — kdcube ECS deployment
# Fill in these values before running aws ecs register-task-definition
# =============================================================================

## AWS account & region
ACCOUNT_ID=          # 12-digit AWS account ID
REGION=              # e.g. eu-central-1

## ECR image tags (use a pinned semver, not "latest", in production)
TAG=                 # e.g. v1.2.3

## VPC & networking
VPC_ID=                  # vpc-xxxxxxxxxxxxxxxxx
PRIVATE_SUBNET_1=        # subnet-xxxxxxxxxxxxxxxxx  (AZ-a)
PRIVATE_SUBNET_2=        # subnet-xxxxxxxxxxxxxxxxx  (AZ-b)

## Security groups — create two:
# SG_PROXY  : inbound TCP 80 from ALB SG only
# SG_APP    : inbound TCP 80/8010/8020/8000 from SG_PROXY only
SG_PROXY=            # sg-xxxxxxxxxxxxxxxxx
SG_APP=              # sg-xxxxxxxxxxxxxxxxx

## ALB target group (for web-proxy service only)
TG_ID=               # xxxxxxxxxxxxxxxx — from ALB target group ARN

## Cloud Map service IDs (output of setup-cloud-map.sh)
SD_SERVICE_ID_PROXY=          # srv-xxxxxxxxxxxxxxxx
SD_SERVICE_ID_WEB_UI=         # srv-xxxxxxxxxxxxxxxx
SD_SERVICE_ID_CHAT_INGRESS=   # srv-xxxxxxxxxxxxxxxx
SD_SERVICE_ID_CHAT_PROC=      # srv-xxxxxxxxxxxxxxxx
SD_SERVICE_ID_PROXYLOGIN=     # srv-xxxxxxxxxxxxxxxx
SD_SERVICE_ID_KB=             # srv-xxxxxxxxxxxxxxxx  (optional)

## Managed infrastructure endpoints
RDS_ENDPOINT=               # e.g. kdcube.xxxxxxxx.<REGION>.rds.amazonaws.com
ELASTICACHE_REDIS_ENDPOINT= # e.g. kdcube.xxxxxx.0001.euc1.cache.amazonaws.com

## EFS (chat-proc and kb only)
EFS_FS_ID=           # fs-xxxxxxxxxxxxxxxxx
EFS_ACCESS_POINT_ID= # fsap-xxxxxxxxxxxxxxxxx

## Cognito (proxylogin)
COGNITO_USER_POOL_ID=    # <REGION>_xxxxxxxxx
COGNITO_APP_CLIENT_ID=   # xxxxxxxxxxxxxxxxxxxxxxxxxx

## Secrets Manager ARNs — create these before registering task definitions:
#   kdcube/db-password
#   kdcube/secret-key
#   kdcube/anthropic-api-key
#   kdcube/openai-api-key
#   kdcube/cognito-client-secret
#   kdcube/session-secret

# =============================================================================
# Quick substitution (bash) — run from the ecs-task-defs/ directory:
# =============================================================================
# for f in task-def-*.json; do
#   sed -i \
#     -e "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" \
#     -e "s/<REGION>/$REGION/g" \
#     -e "s/<TAG>/$TAG/g" \
#     -e "s/<RDS_ENDPOINT>/$RDS_ENDPOINT/g" \
#     -e "s/<ELASTICACHE_REDIS_ENDPOINT>/$ELASTICACHE_REDIS_ENDPOINT/g" \
#     -e "s/<EFS_FS_ID>/$EFS_FS_ID/g" \
#     -e "s/<EFS_ACCESS_POINT_ID>/$EFS_ACCESS_POINT_ID/g" \
#     -e "s/<COGNITO_USER_POOL_ID>/$COGNITO_USER_POOL_ID/g" \
#     -e "s/<COGNITO_APP_CLIENT_ID>/$COGNITO_APP_CLIENT_ID/g" \
#     "$f"
# done
#
# Then register each:
# for f in task-def-*.json; do
#   aws ecs register-task-definition --cli-input-json "file://$f" --region $REGION
# done
