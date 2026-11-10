# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/llm/llm_data_model.py

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any, List, Dict, Literal, Union
from pydantic import BaseModel, Field

from kdcube_ai_app.infra.accounting import ServiceUsage
from kdcube_ai_app.infra.accounting.usage import split_inclusive_prompt_usage

class AIProviderName(str, Enum):
    open_ai = "openai"
    open_router = "openrouter"
    google = "google"
    anthropic = "anthropic"
    hugging_face = "hugging-face"
    self_hosted = "self-hosted"


class AIProvider(BaseModel):
    """
    Equivalent of your TypeScript AIProvider interface.
      {
        id: string;
        provider: AIProviderName;
        data?: any;
        apiToken?: string;
        inferenceEndpoint?: string;
      }
    """
    id: Optional[int] = None
    provider: AIProviderName
    data: Optional[Dict[str, Any]] = None
    apiToken: Optional[str] = None
    inferenceEndpoint: Optional[str] = None

class ExpertRoleName(str, Enum):
    text_generation = "text-generation"
    knowledge_generation = "knowledge-generation"
    summarization = "summarization"
    embedding = "embedding"
    can_be_examined = "can-be-examined"
    examiner = "examiner"
    can_be_fine_tuned = "can-be-fine-tuned"


#
# 2) Data Models
#
class ModelEventType(str, Enum):
    creation = "creation"
    fine_tune = "fine-tune"
    exam = "exam"
    export = "export"
    other = "other"

class ModelEvent(BaseModel):
    """
    A single recorded event in the model’s “biography.”
    """
    id: int
    type: ModelEventType
    timestamp: str
    description: str
    data: Optional[Dict[str, Any]] = None
    modelId: str


class ExpertRole(BaseModel):
    """
    Equivalent of your TypeScript ExpertRole interface:
      {
        id: string
        name: "text-generation" | "knowledge-generation" | ...
        description: string
      }
    """
    id: int
    arn: Optional[str] = None
    name: ExpertRoleName
    description: str

class Expert(BaseModel):
    id: Optional[int] = None
    systemName: str
    arn: Optional[str] = None
    description: Optional[str] = None
    type: Optional[Literal["machine", "human"]] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    roles: Optional[List[ExpertRole]] = None
    profile: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Dict[str, Union[List, str, Dict, int]] = Field(default_factory=dict)

class ModelRecord(Expert):
    modelType: Optional[str] = None
    status: Optional[str] = None  # e.g. "active" | "archived" | etc.
    systemName: str

    baselineModelId: Optional[str] = None
    fineTuningDatasetId: Optional[str] = None

    provider: AIProvider
    embedding: Optional[Union[list[float], str]] = None

class Message(BaseModel):
    role: Literal["system", "user", "assistant", "developer"]
    content: Union[str, dict]
    cache_strategy: Optional[str] = None

class ContentType(Enum):
    """Types of content in AI responses."""
    TEXT = "text"
    IMAGE = "image"
    TOOL_USE = "tool_use"
    UNKNOWN = "unknown"


@dataclass
class ContentBlock:
    """Standardized content block for any modality."""
    type: ContentType
    content: str  # Text content or description
    raw_data: Any = None  # Original data for advanced use

    @classmethod
    def from_text(cls, text: str) -> 'ContentBlock':
        """Create text content block."""
        return cls(type=ContentType.TEXT, content=text)

    @classmethod
    def from_anthropic_block(cls, block: Any) -> 'ContentBlock':
        """Create from Anthropic TextBlock or other block types."""
        if hasattr(block, 'type'):
            if block.type == 'text':
                return cls(
                    type=ContentType.TEXT,
                    content=block.text,
                    raw_data=block
                )
            elif block.type == 'tool_use':
                return cls(
                    type=ContentType.TOOL_USE,
                    content=f"Tool: {getattr(block, 'name', 'unknown')}",
                    raw_data=block
                )

        # Fallback
        return cls(
            type=ContentType.UNKNOWN,
            content=str(block),
            raw_data=block
        )


@dataclass
class TokenUsage:
    """Standardized token usage across providers."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self):
        """Calculate total if not provided."""
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens

@dataclass
class BatchResultWrapper:
    """
    Clean, standardized wrapper for batch processing results.
    Eliminates confusion from provider-specific nested structures.
    """
    # Core identifiers
    message_id: str  # Your original message ID (msg1, msg2, etc.)
    provider_message_id: Optional[str] = None  # Provider's internal ID

    # Result data
    success: bool = False
    content_blocks: List[ContentBlock] = None
    usage: Optional[TokenUsage] = None
    error_message: Optional[str] = None

    # Metadata
    model: Optional[str] = None
    provider: Optional[str] = None
    raw_result: Any = None  # Original result for debugging

    def __post_init__(self):
        """Initialize empty lists."""
        if self.content_blocks is None:
            self.content_blocks = []

    @property
    def text_content(self) -> str:
        """Get all text content as a single string."""
        text_blocks = [block.content for block in self.content_blocks
                       if block.type == ContentType.TEXT]
        return "\n".join(text_blocks)

    @property
    def has_error(self) -> bool:
        """Check if this result has an error."""
        return not self.success or self.error_message is not None

    def get_content_by_type(self, content_type: ContentType) -> List[ContentBlock]:
        """Get all content blocks of a specific type."""
        return [block for block in self.content_blocks if block.type == content_type]

    @classmethod
    def from_anthropic_batch_message(cls, batch_message: 'BatchMessage') -> 'BatchResultWrapper':
        """Create wrapper from Anthropic BatchMessage result."""
        wrapper = cls(message_id=batch_message.id)

        if batch_message.error:
            wrapper.success = False
            wrapper.error_message = str(batch_message.error)
            wrapper.raw_result = batch_message.error
            return wrapper

        # Check native_result first (has full metadata), then fallback to result
        native_result = getattr(batch_message, 'native_result', None)
        result_data = native_result if native_result else batch_message.result

        if not result_data:
            wrapper.success = False
            wrapper.error_message = "No result available"
            return wrapper

        wrapper.raw_result = result_data

        # Handle MessageBatchSucceededResult from native_result
        if hasattr(result_data, 'type') and result_data.type == 'succeeded':
            wrapper.success = True
            wrapper.provider = "anthropic"

            # Extract message data
            if hasattr(result_data, 'message'):
                message = result_data.message
                wrapper.provider_message_id = getattr(message, 'id', None)
                wrapper.model = getattr(message, 'model', None)

                # Extract content blocks
                if hasattr(message, 'content') and message.content:
                    for block in message.content:
                        wrapper.content_blocks.append(
                            ContentBlock.from_anthropic_block(block)
                        )

                # Extract usage
                if hasattr(message, 'usage') and message.usage:
                    usage = message.usage
                    wrapper.usage = TokenUsage(
                        input_tokens=getattr(usage, 'input_tokens', 0),
                        output_tokens=getattr(usage, 'output_tokens', 0),
                        cache_creation_tokens=getattr(usage, 'cache_creation_input_tokens', 0),
                        cache_read_tokens=getattr(usage, 'cache_read_input_tokens', 0)
                    )
        elif isinstance(result_data, str):
            # Fallback: simple string result (from batch_message.result)
            wrapper.success = True
            wrapper.provider = "anthropic"
            wrapper.content_blocks.append(ContentBlock.from_text(result_data))
        else:
            wrapper.success = False
            wrapper.error_message = f"Unexpected result type: {getattr(result_data, 'type', type(result_data).__name__)}"

        return wrapper

    @classmethod
    def from_openai_batch_message(cls, batch_message: 'BatchMessage') -> 'BatchResultWrapper':
        """Create wrapper from OpenAI BatchMessage result."""
        wrapper = cls(message_id=batch_message.id)
        wrapper.provider = "openai"

        if batch_message.error:
            wrapper.success = False
            wrapper.error_message = str(batch_message.error)
            wrapper.raw_result = batch_message.error
            return wrapper

        # Check native_result first (has full metadata), then fallback to result
        native_result = getattr(batch_message, 'native_result', None)

        if native_result and isinstance(native_result, dict):
            # Use native_result which has the full structure
            wrapper.raw_result = native_result

            try:
                # Extract provider message ID
                wrapper.provider_message_id = native_result.get('id')

                # Navigate to response body
                response = native_result.get('response', {})
                if response.get('status_code') != 200:
                    wrapper.success = False
                    wrapper.error_message = f"HTTP {response.get('status_code', 'unknown')}"
                    return wrapper

                body = response.get('body', {})

                # Extract model and provider message ID from response
                wrapper.model = body.get('model')
                if not wrapper.provider_message_id:
                    wrapper.provider_message_id = body.get('id')

                # Extract content from choices
                choices = body.get('choices', [])
                if choices:
                    choice = choices[0]  # Take first choice
                    message_content = choice.get('message', {}).get('content', '')

                    if message_content:
                        wrapper.content_blocks.append(
                            ContentBlock.from_text(message_content)
                        )
                        wrapper.success = True

                # Extract usage
                usage_data = body.get('usage', {})
                if usage_data:
                    # prompt_tokens includes the cached tokens; book only the
                    # uncached part as input so cached tokens are billed once.
                    split = split_inclusive_prompt_usage(usage_data)
                    wrapper.usage = TokenUsage(
                        input_tokens=split.get('prompt_tokens', 0),
                        output_tokens=usage_data.get('completion_tokens', 0),
                        total_tokens=usage_data.get('total_tokens', 0),
                        cache_creation_tokens=0,  # OpenAI doesn't provide this
                        cache_read_tokens=split.get('cache_read_input_tokens', 0)
                    )

            except Exception as e:
                wrapper.success = False
                wrapper.error_message = f"Error parsing OpenAI native_result: {e}"

        elif batch_message.result:
            # Fallback to simple result if native_result not available
            wrapper.raw_result = batch_message.result

            if isinstance(batch_message.result, str):
                # Simple string result
                wrapper.content_blocks.append(ContentBlock.from_text(batch_message.result))
                wrapper.success = True
            elif isinstance(batch_message.result, dict) and 'response' in batch_message.result:
                # Legacy format handling
                response_body = batch_message.result['response'].get('body', {})

                wrapper.model = response_body.get('model')
                wrapper.provider_message_id = response_body.get('id')

                choices = response_body.get('choices', [])
                if choices:
                    choice = choices[0]
                    message_content = choice.get('message', {}).get('content', '')

                    if message_content:
                        wrapper.content_blocks.append(
                            ContentBlock.from_text(message_content)
                        )
                        wrapper.success = True

                usage_data = response_body.get('usage', {})
                if usage_data:
                    wrapper.usage = TokenUsage(
                        input_tokens=usage_data.get('prompt_tokens', 0),
                        output_tokens=usage_data.get('completion_tokens', 0),
                        total_tokens=usage_data.get('total_tokens', 0)
                    )
            else:
                wrapper.success = False
                wrapper.error_message = f"Unexpected OpenAI result format: {type(batch_message.result)}"
        else:
            wrapper.success = False
            wrapper.error_message = "No result or native_result available"

        return wrapper

    @classmethod
    def from_batch_message(cls, batch_message: 'BatchMessage', provider: str = None) -> 'BatchResultWrapper':
        """
        Auto-detect provider and create appropriate wrapper.

        Args:
            batch_message: The BatchMessage with results
            provider: Optional provider hint ("openai" or "anthropic")
        """
        # Try to auto-detect provider from result structure
        if provider is None:
            native_result = getattr(batch_message, 'native_result', None)

            # Check native_result for provider hints
            if native_result:
                if hasattr(native_result, 'type') and native_result.type in ['succeeded', 'errored', 'expired']:
                    provider = "anthropic"
                elif isinstance(native_result, dict) and 'response' in native_result:
                    provider = "openai"

            # Fallback to checking regular result
            if provider is None and batch_message.result:
                if (hasattr(batch_message.result, 'type') and
                    batch_message.result.type in ['succeeded', 'errored', 'expired']):
                    provider = "anthropic"
                else:
                    provider = "openai"

            # Default fallback
            if provider is None:
                provider = "openai"

        if provider == "anthropic":
            return cls.from_anthropic_batch_message(batch_message)
        elif provider == "openai":
            return cls.from_openai_batch_message(batch_message)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "message_id": self.message_id,
            "provider_message_id": self.provider_message_id,
            "success": self.success,
            "content_blocks": [
                {
                    "type": block.type.value,
                    "content": block.content
                } for block in self.content_blocks
            ],
            "text_content": self.text_content,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cache_creation_tokens": self.usage.cache_creation_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens,
                "total_tokens": self.usage.total_tokens
            } if self.usage else None,
            "error_message": self.error_message,
            "model": self.model,
            "provider": self.provider,
            "has_error": self.has_error
        }

    def __str__(self) -> str:
        """Clean string representation."""
        if self.has_error:
            return f"BatchResult(id={self.message_id}, ERROR: {self.error_message})"
        else:
            content_preview = self.text_content[:100] + "..." if len(self.text_content) > 100 else self.text_content
            tokens_info = f", tokens={self.usage.total_tokens}" if self.usage else ""
            return f"BatchResult(id={self.message_id}, model={self.model}, content='{content_preview}'{tokens_info})"

@dataclass
class StreamingResultWrapper:
    """
    Clean, standardized wrapper for streaming results.
    Similar to BatchResultWrapper but optimized for streaming responses.
    """
    # Core identifiers
    message_id: str  # Your original message ID
    provider_message_id: Optional[str] = None  # Provider's internal ID

    # Result data
    success: bool = False
    content: str = ""  # Simplified - just the text content
    usage: Optional[TokenUsage] = None
    error_message: Optional[str] = None

    # Metadata
    model: Optional[str] = None
    provider: Optional[str] = None
    raw_usage: Any = None  # Original usage object for debugging

    @property
    def has_error(self) -> bool:
        """Check if this result has an error."""
        return not self.success or self.error_message is not None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "message_id": self.message_id,
            "provider_message_id": self.provider_message_id,
            "success": self.success,
            "content": self.content,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cache_creation_tokens": self.usage.cache_creation_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens,
                "total_tokens": self.usage.total_tokens
            } if self.usage else None,
            "error_message": self.error_message,
            "model": self.model,
            "provider": self.provider,
            "has_error": self.has_error
        }

    def __str__(self) -> str:
        """Clean string representation."""
        if self.has_error:
            return f"StreamingResult(id={self.message_id}, ERROR: {self.error_message})"
        else:
            content_preview = self.content[:100] + "..." if len(self.content) > 100 else self.content
            return f"StreamingResult(id={self.message_id}, content='{content_preview}', tokens={self.usage.total_tokens if self.usage else 0})"

@dataclass
class EmbeddingUsage:
    """Embedding-specific usage metrics."""
    embedding_tokens: int = 0
    embedding_dimensions: int = 0
    text_length: int = 0
    requests: int = 1

    # Cost if available (can be calculated based on provider pricing)
    cost_usd: Optional[float] = None


@dataclass
class EmbeddingResultWrapper:
    """
    Standardized wrapper for embedding service results.
    Similar to BatchResultWrapper/StreamingResultWrapper but for embeddings.
    """
    # Core identifiers
    segment_id: str  # Your segment ID
    provider_request_id: Optional[str] = None  # Provider's internal ID if available

    # Result data
    success: bool = False
    embedding: Optional[List[float]] = None
    embedding_string: Optional[str] = None  # Serialized version
    usage: Optional[EmbeddingUsage] = None
    error_message: Optional[str] = None

    # Metadata
    model: Optional[str] = None
    provider: Optional[str] = None
    embedding_dimensions: int = 0
    text_length: int = 0
    raw_response: Any = None  # Original response for debugging

    @property
    def has_error(self) -> bool:
        """Check if this result has an error."""
        return not self.success or self.error_message is not None

    @classmethod
    def from_success(cls,
                     segment_id: str,
                     embedding: List[float],
                     model: str,
                     provider: str,
                     text_length: int,
                     embedding_tokens: int = 0,
                     provider_request_id: str = None,
                     raw_response: Any = None) -> 'EmbeddingResultWrapper':
        """Create successful embedding result."""

        embedding_dimensions = len(embedding) if embedding else 0

        usage = EmbeddingUsage(
            embedding_tokens=embedding_tokens or text_length,  # Fallback to text length
            embedding_dimensions=embedding_dimensions,
            text_length=text_length,
            requests=1
        )
        from kdcube_ai_app.infra.embedding.embedding import get_embedding, convert_embedding_to_string
        return cls(
            segment_id=segment_id,
            provider_request_id=provider_request_id,
            success=True,
            embedding=embedding,
            embedding_string=convert_embedding_to_string(embedding),
            usage=usage,
            model=model,
            provider=provider,
            embedding_dimensions=embedding_dimensions,
            text_length=text_length,
            raw_response=raw_response
        )

    @classmethod
    def from_error(cls,
                   segment_id: str,
                   error_message: str,
                   model: str = None,
                   provider: str = None,
                   text_length: int = 0,
                   raw_response: Any = None) -> 'EmbeddingResultWrapper':
        """Create failed embedding result."""

        return cls(
            segment_id=segment_id,
            success=False,
            error_message=error_message,
            model=model,
            provider=provider,
            text_length=text_length,
            usage=EmbeddingUsage(text_length=text_length, requests=1),
            raw_response=raw_response
        )

    def to_service_usage(self) -> ServiceUsage:
        """Convert to ServiceUsage for usage tracking."""
        if not self.usage:
            return ServiceUsage(requests=1)

        return ServiceUsage(
            embedding_tokens=self.usage.embedding_tokens,
            embedding_dimensions=self.usage.embedding_dimensions,
            requests=self.usage.requests,
            cost_usd=self.usage.cost_usd
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "segment_id": self.segment_id,
            "provider_request_id": self.provider_request_id,
            "success": self.success,
            "embedding_dimensions": self.embedding_dimensions,
            "text_length": self.text_length,
            "usage": {
                "embedding_tokens": self.usage.embedding_tokens,
                "embedding_dimensions": self.usage.embedding_dimensions,
                "text_length": self.usage.text_length,
                "requests": self.usage.requests,
                "cost_usd": self.usage.cost_usd
            } if self.usage else None,
            "error_message": self.error_message,
            "model": self.model,
            "provider": self.provider,
            "has_error": self.has_error
        }

    def __str__(self) -> str:
        """Clean string representation."""
        if self.has_error:
            return f"EmbeddingResult(id={self.segment_id}, ERROR: {self.error_message})"
        else:
            return f"EmbeddingResult(id={self.segment_id}, model={self.model}, dims={self.embedding_dimensions}, tokens={self.usage.embedding_tokens if self.usage else 0})"


# Convenience function for easy usage
def wrap_batch_results(batch_messages: List['BatchMessage'], provider: str = None) -> List[BatchResultWrapper]:
    """
    Convert list of BatchMessages to clean BatchResultWrapper objects.

    Args:
        batch_messages: List of BatchMessage objects with results
        provider: Optional provider hint ("openai" or "anthropic")

    Returns:
        List of clean, standardized BatchResultWrapper objects
    """
    return [
        BatchResultWrapper.from_batch_message(msg, provider)
        for msg in batch_messages
    ]
