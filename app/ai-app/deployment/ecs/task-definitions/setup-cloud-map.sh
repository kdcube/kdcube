#!/usr/bin/env bash
# =============================================================================
# Cloud Map setup — kdcube.local private DNS namespace
# Run once per environment. Replace <PLACEHOLDER> values.
# =============================================================================
set -euo pipefail

REGION="<REGION>"
VPC_ID="<VPC_ID>"
NAMESPACE="kdcube.local"

# ---------------------------------------------------------------------------
# 1. Create private DNS namespace
# ---------------------------------------------------------------------------
NAMESPACE_ID=$(aws servicediscovery create-private-dns-namespace \
  --name "$NAMESPACE" \
  --vpc "$VPC_ID" \
  --region "$REGION" \
  --query 'OperationId' --output text)

echo "Waiting for namespace creation (operation: $NAMESPACE_ID) ..."
aws servicediscovery get-operation --operation-id "$NAMESPACE_ID" \
  --region "$REGION" --query 'Operation.Status' --output text

NAMESPACE_RESOURCE_ID=$(aws servicediscovery list-namespaces \
  --region "$REGION" \
  --query "Namespaces[?Name=='$NAMESPACE'].Id" \
  --output text)
echo "Namespace ID: $NAMESPACE_RESOURCE_ID"

# ---------------------------------------------------------------------------
# 2. Register services (one per ECS service)
#    DNS: <service-name>.kdcube.local
#    Matches the upstream names used in nginx.conf
# ---------------------------------------------------------------------------
register_service() {
  local NAME=$1
  aws servicediscovery create-service \
    --name "$NAME" \
    --namespace-id "$NAMESPACE_RESOURCE_ID" \
    --dns-config "NamespaceId=$NAMESPACE_RESOURCE_ID,RoutingPolicy=MULTIVALUE,DnsRecords=[{Type=A,TTL=10}]" \
    --health-check-custom-config "FailureThreshold=1" \
    --region "$REGION" \
    --query 'Service.Arn' --output text
}

# These names must match the nginx upstream server directives exactly:
#   upstream web_ui      { server web-ui.kdcube.local:80;        }
#   upstream chat_api    { server chat-ingress.kdcube.local:8010; }
#   upstream chat_proc   { server chat-proc.kdcube.local:8020;   }
#   upstream proxy_login { server proxylogin.kdcube.local:80;    }
#   upstream kb_api      { server kb.kdcube.local:8000;          }  # optional

echo "Registering Cloud Map services ..."
register_service "web-ui"
register_service "chat-ingress"
register_service "chat-proc"
register_service "proxylogin"
# Uncomment when KB is active:
# register_service "kb"

echo ""
echo "Done. Update each ECS service definition with the matching serviceRegistry ARN."
echo ""
echo "nginx upstream block to use in nginx.conf (ECS version):"
echo "-----------------------------------------------------------"
cat << 'NGINX'
    # VPC DNS resolver — mandatory for Lua subrequests (unmask_token)
    resolver 169.254.169.253 valid=10s;
    resolver_timeout 5s;

    upstream web_ui {
        server web-ui.kdcube.local:80;
    }

    upstream chat_api {
        server chat-ingress.kdcube.local:8010;
    }

    upstream chat_proc {
        server chat-proc.kdcube.local:8020;
    }

    upstream proxy_login {
        server proxylogin.kdcube.local:80;
    }

    # Uncomment when KB is active:
    # upstream kb_api {
    #     server kb.kdcube.local:8000;
    # }

    # Uncomment when behind ALB:
    # real_ip_header    X-Forwarded-For;
    # set_real_ip_from  10.0.0.0/8;    # replace with your VPC/ALB CIDR
    # real_ip_recursive on;
NGINX
