# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/middleware/simple_idp.py
"""
Framework-agnostic Simple IDP implementation
"""
from typing import Dict, Optional

from kdcube_ai_app.auth.AuthManager import AuthManager, User, AuthenticationError
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.config_scopes import SIMPLE_IDP_STORE_PATH
from kdcube_ai_app.apps.middleware.simple_idp_registry import (
    DEFAULT_SIMPLE_IDP_USERS,
    SimpleIDPRegistry,
    get_simple_idp_registry,
)


# Simple user database - stored as JSON file
IDP_DB_PATH = get_settings().AUTH.IDP.local.IDP_DB_PATH or SIMPLE_IDP_STORE_PATH

# Default users
DEFAULT_USERS = DEFAULT_SIMPLE_IDP_USERS


class SimpleIDPUser(User):
    """Extended User class for Simple IDP"""
    sub: str
    service_user: bool = False


class SimpleIDP(AuthManager):
    """Framework-agnostic Simple IDP for testing"""

    def __init__(
        self,
        send_validation_error_details: bool = False,
        service_user_token: Optional[str] = None,
        *,
        idp_db_path: Optional[str] = None,
        registry: Optional[SimpleIDPRegistry] = None,
    ):
        super().__init__(send_validation_error_details)
        self.registry = registry or get_simple_idp_registry(idp_db_path or IDP_DB_PATH, default_users=DEFAULT_USERS)
        self.service_user_token = service_user_token

    def _load_users(self) -> Dict:
        """Load users from JSON file or create default"""
        return self.registry.list_users_sync()

    def _save_users(self, users: Dict):
        """Save users to JSON file"""
        for token, user_data in users.items():
            self.registry.upsert_user_sync(token, user_data)

    async def authenticate(self, token: str) -> SimpleIDPUser:
        """
        Authenticate a token and return user info.

        Args:
            token: The authentication token

        Returns:
            SimpleIDPUser object if authentication succeeds

        Raises:
            AuthenticationError: If authentication fails
        """
        if not token:
            raise AuthenticationError("No token provided")

        user_data = await self.registry.get_user(token)

        if not user_data:
            raise AuthenticationError("Invalid token")

        # Create user object
        return SimpleIDPUser(
            username=user_data.get("username"),
            email=user_data.get("email"),
            name=user_data.get("name"),
            roles=user_data.get("roles", []),
            permissions=user_data.get("permissions", []),
            sub=user_data.get("sub"),
            service_user=user_data.get("service_user", False),
        )

    # Utility methods for managing the simple IDP
    def get_all_users(self) -> Dict:
        """Get all users (for debugging) - excludes tokens"""
        users_db = self.registry.list_users_sync()
        return {token: {k: v for k, v in user.items() if k != 'token'}
                for token, user in users_db.items()}

    def add_user(self, token: str, user_data: Dict) -> bool:
        """Add a new user to the database"""
        try:
            self.registry.upsert_user_sync(token, user_data)
            return True
        except Exception as e:
            print(f"Error adding user: {e}")
            return False

    def remove_user(self, token: str) -> bool:
        """Remove a user from the database"""
        try:
            return self.registry.remove_user_sync(token)
        except Exception as e:
            print(f"Error removing user: {e}")
            return False

    def update_user(self, token: str, user_data: Dict) -> bool:
        """Update an existing user in the database"""
        try:
            existing = self.registry.list_users_sync().get(token)
            if existing:
                existing.update(user_data)
                self.registry.upsert_user_sync(token, existing)
                return True
            return False
        except Exception as e:
            print(f"Error updating user: {e}")
            return False

    async def register_user(self, token: str, user_data: Dict) -> SimpleIDPUser:
        """Register or replace a SimpleIDP user through the shared registry."""
        await self.registry.upsert_user(token, user_data)
        return await self.authenticate(token)

    async def get_service_token(self) -> str:
        return self.service_user_token

# generate_test_tokens.py
"""
Generate JWT tokens for test users
"""
import jwt
import json
import time
from datetime import datetime, timedelta

# Secret key for signing JWTs (in production, this should be from environment)
SECRET_KEY = "test-secret-key-for-development-only"
ALGORITHM = "HS256"

def create_jwt_token(user_data: dict, expires_hours: int = 24) -> str:
    """Create a JWT token for a user"""

    # JWT payload
    payload = {
        "sub": user_data["sub"],
        "username": user_data["username"],
        "email": user_data["email"],
        "roles": user_data["roles"],
        "permissions": user_data["permissions"],
        "iat": int(time.time()),  # Issued at
        "exp": int(time.time()) + (expires_hours * 3600),  # Expires
        "iss": "kdcube-test-idp",  # Issuer
        "aud": "kdcube-chat"  # Audience
    }

    # Create JWT token
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token

def generate_test_tokens():
    """Generate tokens for both test users"""

    # Test users
    users = {
        "admin": {
            "sub": "admin-user-1",
            "username": "admin",
            "email": "admin@test.com",
            "roles": ["kdcube:role:super-admin"],
            "permissions": [
                "kdcube:*:knowledge:_base:*;read;write;delete",
                "kdcube:*:monitoring:*;read"
            ]
        },
        "chatuser": {
            "sub": "chat-user-1",
            "username": "chatuser",
            "email": "chat@test.com",
            "roles": ["kdcube:role:registered"],
            "permissions": [
                "kdcube:*:knowledge:_base:*;read"
            ]
        }
    }

    # Generate tokens
    tokens = {}
    for user_type, user_data in users.items():
        token = create_jwt_token(user_data)
        tokens[user_type] = {
            "token": token,
            "user_data": user_data
        }

    return tokens

def save_tokens_to_file(tokens: dict, filename: str = "test_tokens.json"):
    """Save tokens to a JSON file for easy reference"""

    # Format for easy copy-paste
    output = {
        "tokens": {
            "admin_token": tokens["admin"]["token"],
            "chat_token": tokens["chatuser"]["token"]
        },
        "curl_examples": {
            "admin_monitoring": f'curl -H "Authorization: Bearer {tokens["admin"]["token"]}" "http://localhost:8010/monitoring/system"',
            "admin_chat": f'curl -H "Authorization: Bearer {tokens["admin"]["token"]}" -X POST "http://localhost:8010/landing/chat" -H "Content-Type: application/json" -d \'{{"message": "Hello as admin", "config": {{}}}}\'',
            "chat_user": f'curl -H "Authorization: Bearer {tokens["chatuser"]["token"]}" -X POST "http://localhost:8010/landing/chat" -H "Content-Type: application/json" -d \'{{"message": "Hello as chat user", "config": {{}}}}\''
        },
        "user_details": {
            "admin": tokens["admin"]["user_data"],
            "chatuser": tokens["chatuser"]["user_data"]
        }
    }

    with open(filename, 'w') as f:
        json.dump(output, f, indent=2)

    return filename

if __name__ == "__main__":
    print("Generating JWT tokens for test users...")

    # Generate tokens
    tokens = generate_test_tokens()

    # Save to file
    filename = save_tokens_to_file(tokens)

    print(f"\nTokens saved to: {filename}")
    print("\n" + "="*50)
    print("TEST TOKENS GENERATED")
    print("="*50)

    print(f"\n🔑 ADMIN TOKEN:")
    print(f"{tokens['admin']['token']}")

    print(f"\n🔑 CHAT USER TOKEN:")
    print(f"{tokens['chatuser']['token']}")

    print(f"\n📋 COPY-PASTE CURL COMMANDS:")
    print(f"\n# Test admin access to monitoring:")
    print(f'curl -H "Authorization: Bearer {tokens["admin"]["token"]}" "http://localhost:8010/monitoring/system"')

    print(f"\n# Test admin chat:")
    print(f'curl -H "Authorization: Bearer {tokens["admin"]["token"]}" -X POST "http://localhost:8010/landing/chat" -H "Content-Type: application/json" -d \'{{"message": "Hello as admin", "config": {{}}}}\'')

    print(f"\n# Test chat user (should fail for monitoring):")
    print(f'curl -H "Authorization: Bearer {tokens["chatuser"]["token"]}" "http://localhost:8010/monitoring/system"')

    print(f"\n# Test chat user chat (should work):")
    print(f'curl -H "Authorization: Bearer {tokens["chatuser"]["token"]}" -X POST "http://localhost:8010/landing/chat" -H "Content-Type: application/json" -d \'{{"message": "Hello as chat user", "config": {{}}}}\'')

    print(f"\n📄 Full details saved to: {filename}")
    print("\n" + "="*50)
