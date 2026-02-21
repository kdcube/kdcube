# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# auth/AuthManager.py
import logging
from abc import abstractmethod, ABCMeta
from typing import Optional, Tuple

from pydantic import BaseModel

logger = logging.getLogger("AuthManager")

HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403

PRIVILEGED_ROLES = {"kdcube:role:super-admin", "kdcube:role:admin"}
PAID_ROLES = {"kdcube:role:paid"}
REGISTERED_ROLE = "kdcube:role:chat-user"

class User(BaseModel):
    username: str = None
    email: Optional[str] = None
    name: Optional[str] = None
    roles: Optional[list] = []
    permissions: Optional[list] = []
    user_type: Optional[str] = None

    @property
    def id(self):
        return self.username

class AuthenticationError(Exception):
    """Raised when authentication fails"""
    def __init__(self, message: str, code: int = HTTP_401_UNAUTHORIZED):
        self.message = message
        self.code = code
        super().__init__(message)


class AuthorizationError(Exception):
    """Raised when authorization fails"""
    def __init__(self, message: str, code: int = HTTP_403_FORBIDDEN):
        self.message = message
        self.code = code
        super().__init__(message)


class RequirementValidationError:
    message: str
    code: int

    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code


class RequirementBase(BaseModel, metaclass=ABCMeta):
    class Config:
        # Forbid any extra attributes on requirements
        extra = "forbid"

    @abstractmethod
    def validate_requirement(self, user: User) -> Optional[RequirementValidationError]:
        raise NotImplementedError


class RequireUser(RequirementBase):
    def validate_requirement(self, user: "User") -> Optional[RequirementValidationError]:
        if user is None:
            return RequirementValidationError("User is required.", HTTP_401_UNAUTHORIZED)
        user_type = getattr(user, "user_type", None)
        if isinstance(user_type, str) and user_type.lower() == "anonymous":
            return RequirementValidationError("User is required.", HTTP_401_UNAUTHORIZED)
        roles = getattr(user, "roles", None) or []
        if not roles:
            return RequirementValidationError("User has no roles assigned.", HTTP_403_FORBIDDEN)
        if any(isinstance(r, str) and r.lower() == "anonymous" for r in roles):
            return RequirementValidationError("User is required.", HTTP_401_UNAUTHORIZED)
        return None


class RequireRoles(RequirementBase):
    roles: Tuple[str, ...]
    require_all: bool = True

    def __init__(self, /, *roles: str, require_all: bool = True):
        # Pass them into BaseModel
        super().__init__(roles=roles, require_all=require_all)

    def validate_requirement(self, user: "User") -> Optional[RequirementValidationError]:
        if user is None:
            return RequirementValidationError("User is required.", HTTP_401_UNAUTHORIZED)

        if not self.roles:
            logger.warning(f"{self} has no roles to check")
            return None

        if not user.roles:
            return RequirementValidationError("User has no roles assigned.", HTTP_403_FORBIDDEN)

        required = set(self.roles)
        actual = set(user.roles)

        if self.require_all:
            missing = required - actual
            if missing:
                return RequirementValidationError(
                    f"User missing required roles: {', '.join(sorted(missing))}",
                    HTTP_403_FORBIDDEN
                )
        else:
            if not (required & actual):
                return RequirementValidationError(
                    f"User must have at least one of these roles: {', '.join(sorted(required))}",
                    HTTP_403_FORBIDDEN
                )

        return None

class RequirePermissions(RequirementBase):
    permissions: Tuple[str, ...]
    require_all: bool = True

    def __init__(self, /, *permissions: str, require_all: bool = True):
        super().__init__(permissions=permissions, require_all=require_all)

    def validate_requirement(self, user: "User") -> Optional[RequirementValidationError]:
        if user is None:
            return RequirementValidationError("User is required.", HTTP_401_UNAUTHORIZED)

        if not self.permissions:
            logger.warning(f"{self} has no permissions to check")
            return None

        if not user.permissions:
            return RequirementValidationError("User has no permissions assigned.", HTTP_403_FORBIDDEN)

        required = set(self.permissions)
        actual = set(user.permissions)

        # Check permission requirements
        if self.require_all:
            missing = required - actual
            if missing:
                return RequirementValidationError(
                    f"User missing required permissions: {', '.join(sorted(missing))}",
                    HTTP_403_FORBIDDEN
                )
        else:
            if not (required & actual):
                return RequirementValidationError(
                    f"User must have at least one of these permissions: {', '.join(sorted(required))}",
                    HTTP_403_FORBIDDEN
                )

        return None


class AuthManager(metaclass=ABCMeta):
    """Framework-agnostic authentication manager"""
    
    def __init__(self, send_validation_error_details: bool = False):
        self.send_validation_error_details = send_validation_error_details

    @abstractmethod
    async def authenticate(self, token: str) -> User:
        """
        Authenticate a token and return a user object.
        
        Args:
            token: The authentication token
            
        Returns:
            User object if authentication succeeds
            
        Raises:
            AuthenticationError: If authentication fails
        """
        pass

    @abstractmethod
    async def get_service_token(self) -> str:
        pass

    def validate_requirements(self, user: User, *requirements: RequirementBase, require_all: bool = True) -> None:
        """
        Validate that a user meets the specified requirements.
        
        Args:
            user: The user to validate
            requirements: List of requirements to check
            require_all: If True, all requirements must be met. If False, at least one must be met.
            
        Raises:
            AuthorizationError: If authorization fails
        """
        if require_all:
            # All requirements must be satisfied
            for requirement in requirements:
                validation_error = requirement.validate_requirement(user)
                if validation_error:
                    raise AuthorizationError(
                        validation_error.message if self.send_validation_error_details else "Authorization failed",
                        validation_error.code
                    )
        else:
            # At least one requirement must be satisfied
            last_validation_error = None
            for requirement in requirements:
                validation_error = requirement.validate_requirement(user)
                if validation_error:
                    last_validation_error = validation_error
                else:
                    # At least one requirement is satisfied
                    return
            
            # None of the requirements were satisfied
            if last_validation_error:
                raise AuthorizationError(
                    last_validation_error.message if self.send_validation_error_details else "Authorization failed",
                    last_validation_error.code
                )

    async def authenticate_and_authorize(self, token: str, *requirements: RequirementBase, require_all: bool = True) -> User:
        """
        Authenticate a token and validate requirements in one step.
        
        Args:
            token: The authentication token
            requirements: List of requirements to check
            require_all: If True, all requirements must be met. If False, at least one must be met.
            
        Returns:
            User object if both authentication and authorization succeed
            
        Raises:
            AuthenticationError: If authentication fails
            AuthorizationError: If authorization fails
        """
        user = await self.authenticate(token)
        self.validate_requirements(user, *requirements, require_all=require_all)
        return user

    async def authenticate_with_both(self, access_token: str, id_token: Optional[str]) -> User:
        """
        Default behavior: ignore id_token and use access token only.
        Concrete managers (OAuth/Cognito) override to merge identity from ID token.
        """
        return await self.authenticate(access_token)

    async def authenticate_and_authorize_with_both(
            self,
            access_token: str,
            id_token: Optional[str],
            *requirements: RequirementBase,
            require_all: bool = True
    ) -> User:
        user = await self.authenticate_with_both(access_token, id_token)
        self.validate_requirements(user, *requirements, require_all=require_all)
        return user
