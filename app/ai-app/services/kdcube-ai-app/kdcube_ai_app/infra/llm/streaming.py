# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/llm/streaming.py
import json
import asyncio
import traceback
from typing import Union, Dict, Any, Optional, List, Callable
from io import StringIO

import anthropic
import openai
from openai import AsyncOpenAI

from kdcube_ai_app.infra.accounting import track_llm, ServiceUsage, AccountingSystem, with_accounting
from kdcube_ai_app.infra.llm.llm_data_model import (TokenUsage, StreamingResultWrapper,
                                                    ModelRecord, AIProvider, Message,
                                                    AIProviderName)
from kdcube_ai_app.infra.llm.util import (get_system_message,
                                          convert_messages_for_anthropic,
                                          rate_limiter, rate_limited_request,
                                          fix_invalid_json,
                                          fix_invalid_boolean_and_null,
                                          apply_cache_strategy)
from kdcube_ai_app.storage.storage import create_storage_backend


async def create_client(model_record: ModelRecord):
    if model_record.provider.provider == AIProviderName.anthropic:
        client = anthropic.AsyncAnthropic(api_key=model_record.provider.apiToken,
                                          default_headers={
                                              "anthropic-beta": "output-128k-2025-02-19"
                                          })

    elif model_record.provider.provider == AIProviderName.open_ai:
        client = AsyncOpenAI(api_key=model_record.provider.apiToken)
    else:
        raise ValueError(f"Unsupported AI provider: {model_record.provider.provider}")
    return client

class StreamingJSONAggregator:
    """
    A utility class to handle streaming JSON responses.

    This class buffers incoming content, tracks the JSON syntax state,
    and provides a mechanism to detect when a complete, valid JSON object
    has been received, even when the stream is still in progress.
    """

    def __init__(self, on_complete_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        """
        Initialize the aggregator.

        Args:
            on_complete_callback: Optional callback function to be called when a complete JSON is detected
        """
        self.buffer = StringIO()
        self.complete_json = None
        self.on_complete_callback = on_complete_callback

        # JSON parsing state
        self.curly_level = 0
        self.square_level = 0
        self.in_string = False
        self.escape_next = False

        # Remember whether we're in an object or array at the very top
        self.start_char: Optional[str] = None

    def add_chunk(self, chunk: str) -> bool:
        self.buffer.write(chunk)

        # Update JSON syntax tracking state
        for char in chunk:
            # Handle escaping inside strings
            if self.escape_next:
                self.escape_next = False
                continue

            # Toggle in_string state
            if char == '"' and not self.escape_next:
                self.in_string = not self.in_string
            elif char == '\\' and self.in_string:
                self.escape_next = True

            # Only update bracket depths when not in a string
            if not self.in_string:
                # If we haven't seen our first bracket yet, check here
                if self.start_char is None and char in ('{', '['):
                    self.start_char = char

                # Track curly braces
                if char == '{':
                    self.curly_level += 1
                elif char == '}':
                    self.curly_level -= 1

                # Track square brackets
                elif char == '[':
                    self.square_level += 1
                elif char == ']':
                    self.square_level -= 1

                # If we've closed out _both_ levels, we have a top-level JSON
                if self.curly_level == 0 and self.square_level == 0 and self.start_char:
                    try:
                        content = self.buffer.getvalue()

                        # Locate the full JSON slice
                        if self.start_char == '{':
                            start_idx = content.find('{')
                            end_idx   = content.rfind('}') + 1
                        else:
                            start_idx = content.find('[')
                            end_idx   = content.rfind(']') + 1

                        if start_idx >= 0 and end_idx > start_idx:
                            json_str = content[start_idx:end_idx]
                            parsed = json.loads(json_str)

                            self.complete_json = parsed
                            if self.on_complete_callback:
                                self.on_complete_callback(parsed)
                            return True
                    except json.JSONDecodeError:
                        # not quite valid yet—keep buffering
                        pass

        return False

    def get_content(self) -> str:
        return self.buffer.getvalue()

    def get_complete_json(self) -> Optional[Any]:
        return self.complete_json


class StreamingUsageTracker:
    """Track usage information from streaming responses."""

    def __init__(self):
        self.usage = TokenUsage()
        self.provider_message_id = None

    def update_from_anthropic_event(self, event):
        """Update usage from Anthropic stream events."""
        if event.type == "message_start":
            if hasattr(event, 'message') and hasattr(event.message, 'usage'):
                usage = event.message.usage
                self.usage.input_tokens = getattr(usage, 'input_tokens', 0)
                self.usage.cache_creation_tokens = getattr(usage, 'cache_creation_input_tokens', 0)
                self.usage.cache_read_tokens = getattr(usage, 'cache_read_input_tokens', 0)

            if hasattr(event, 'message') and hasattr(event.message, 'id'):
                self.provider_message_id = event.message.id

        elif event.type == "message_delta":
            if hasattr(event, 'usage'):
                self.usage.output_tokens = getattr(event.usage, 'output_tokens', 0)

        elif event.type == "message_stop":
            # Final usage information might be here
            pass

        # Recalculate total
        self.usage.total_tokens = (self.usage.input_tokens +
                                 self.usage.output_tokens +
                                 self.usage.cache_creation_tokens)

    def update_from_openai_chunk(self, chunk):
        """Update usage from OpenAI stream chunks."""
        # OpenAI doesn't provide usage in streaming mode typically
        # Usage information comes at the end if stream_options={"include_usage": True}
        if hasattr(chunk, 'usage') and chunk.usage:
            self.usage.input_tokens = chunk.usage.prompt_tokens or 0
            self.usage.output_tokens = chunk.usage.completion_tokens or 0
            self.usage.total_tokens = chunk.usage.total_tokens or 0

        if hasattr(chunk, 'id'):
            self.provider_message_id = chunk.id

def _streaming_metadata_extractor(*, model=None, messages=None, parse_json=True,
                                  temperature=None, max_tokens=None, system_reasoning=True, **_):
    """
    Minimal, provider-agnostic metadata to attach to accounting events.
    (No seeds here—those come from with_accounting at call sites.)
    """
    try:
        # messages may be a list of our Message model or dicts
        def _text_len(m):
            c = getattr(m, "content", None)
            if c is None and isinstance(m, dict):
                c = m.get("content")
            return len(str(c or ""))

        prompt_chars = sum(_text_len(m) for m in (messages or []))
    except Exception:
        prompt_chars = 0

    return {
        "parse_json": bool(parse_json),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "system_reasoning": bool(system_reasoning),
        "prompt_chars": prompt_chars,
    }

@track_llm(
    # handle the (content, usage, provider_message_id) tuple too
    usage_extractor=lambda result, *a, **kw: (
            ServiceUsage(
                input_tokens=getattr(result[1], "input_tokens", 0),
                output_tokens=getattr(result[1], "output_tokens", 0),
                cache_creation_tokens=getattr(result[1], "cache_creation_tokens", 0),
                cache_read_tokens=getattr(result[1], "cache_read_tokens", 0),
                total_tokens=getattr(result[1], "total_tokens", 0),
                requests=1
            ) if isinstance(result, tuple) and len(result) >= 2 else ServiceUsage(requests=1)
    ),
    metadata_extractor=_streaming_metadata_extractor
)
async def llm_streaming_with_progress(
    model,
    messages,
    parse_json=True,
    temperature=None,
    max_tokens=None,
    on_chunk=None,
    on_complete_json=None,
    system_reasoning=True,
    max_retries=10,  # Maximum number of retries for rate limit errors
    retry_backoff_factor=2.0,  # Exponential backoff factor for retries
    return_usage=False,  # Whether to return usage information
) -> Union[str, Dict[str, Any], tuple]:
    """
    Streaming version with progress reporting, rate limiting, and modern cache support.

    Args:
        model: Model record containing provider details
        messages: List of messages to send
        parse_json: Whether to parse the response as JSON
        temperature: Temperature parameter for the model
        max_tokens: Maximum tokens to generate
        on_chunk: Callback for each received chunk
        on_complete_json: Callback when a complete JSON is detected
        system_reasoning: Whether to enable extended thinking mode
        max_retries: Maximum number of retry attempts for rate limit errors
        retry_backoff_factor: Exponential backoff factor for retry delays
        return_usage: Whether to return usage information as tuple (content, usage, provider_message_id)

    Returns:
        String or parsed JSON response, optionally with usage info as tuple
    """
    # Set default parameters
    if temperature is None:
        temperature = 0.7
    if system_reasoning:
        temperature = 1.0
    if max_tokens is None:
        max_tokens = 64000  # Default to the standard limit

    client = await create_client(model)

    # For tracking retries
    retry_count = 0
    original_max_tokens = max_tokens

    # Function to handle streaming with consistent event handling
    async def process_stream(stream, system_reasoning_enabled, usage_tracker):
        nonlocal result, current_block_type, current_content

        result = ""
        current_block_type = None
        current_content = ""

        if system_reasoning_enabled:
            # For OpenAI stream
            if isinstance(stream, openai.AsyncStream):
                # OpenAI chat.completions API returns ChatCompletionChunk objects
                async for chunk in stream:
                    # Update usage tracking
                    usage_tracker.update_from_openai_chunk(chunk)

                    # Handle the ChatCompletionChunk format
                    if chunk.choices and hasattr(chunk.choices[0], 'delta') and hasattr(chunk.choices[0].delta, 'content'):
                        content = chunk.choices[0].delta.content
                        if content:
                            if on_chunk:
                                on_chunk(content)
                            current_content += content
                            result += content
                            aggregator.add_chunk(content)

                    # Optional: Handle message completion (if needed)
                    if hasattr(chunk, 'finish_reason') and chunk.finish_reason:
                        if on_chunk:
                            on_chunk("\n--- Message complete ---\n")

            # For Anthropic stream
            else:
                async for event in stream:
                    # Update usage tracking for all events
                    usage_tracker.update_from_anthropic_event(event)

                    if event.type == "message_start":
                        if on_chunk:
                            on_chunk("\n--- New message started ---\n")
                            on_chunk(f"\n--- {event} ---\n")
                    # Handle different event types
                    elif event.type == "content_block_start":
                        current_block_type = event.content_block.type
                        if on_chunk:
                            on_chunk(f"\n--- Starting {current_block_type} block ---\n")
                        current_content = ""

                    elif event.type == "content_block_delta":
                        if event.delta.type == "thinking_delta":
                            if event.delta.thinking:
                                if on_chunk:
                                    on_chunk(event.delta.thinking)
                                current_content += event.delta.thinking

                        elif event.delta.type == "text_delta":
                            chunk = event.delta.text if event.delta.text else ""
                            if on_chunk:
                                on_chunk(chunk)
                            current_content += chunk
                            result += chunk
                            aggregator.add_chunk(chunk)

                    elif event.type == "content_block_stop":
                        if on_chunk:
                            on_chunk(f"\n--- Finished {current_block_type} block ---\n")
                        current_block_type = None

                    elif event.type == "message_stop":
                        if on_chunk:
                            on_chunk("\n--- Message complete ---\n")
        else:
            # Simplified event handling when system reasoning is disabled
            # For OpenAI
            if isinstance(stream, openai.AsyncStream):
                async for chunk in stream:
                    # Update usage tracking
                    usage_tracker.update_from_openai_chunk(chunk)

                    if hasattr(chunk.choices[0], 'delta') and hasattr(chunk.choices[0].delta, 'content'):
                        content = chunk.choices[0].delta.content
                        if content:
                            if on_chunk:
                                on_chunk(content)
                            result += content
                            aggregator.add_chunk(content)

            # For Anthropic
            else:
                async for event in stream:
                    # Update usage tracking for all events
                    usage_tracker.update_from_anthropic_event(event)

                    if event.type == "message_start":
                        if on_chunk:
                            on_chunk("\n--- New message started ---\n")
                            on_chunk(f"\n--- {event} ---\n")
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        chunk = event.delta.text if event.delta.text else ""
                        if on_chunk:
                            on_chunk(chunk)
                        result += chunk
                        aggregator.add_chunk(chunk)
                    elif event.type == "message_stop" and on_chunk:
                        on_chunk("\n--- Message complete ---\n")

        return result

    while retry_count <= max_retries:
        try:
            # Extract system message and format other messages
            system_content = get_system_message(messages)
            system = None

            # Apply cache strategy
            if model.provider.provider == AIProviderName.anthropic:
                # Use default cache strategy
                system = [apply_cache_strategy(message) for message in messages if message.role == "system"]
                # in fact, this must be windowed processing of the groups of the messages originated from the same turn
                user_messages = [{
                    "content": [apply_cache_strategy(message) for message in messages if message.role != "system"],
                    "role": "user"
                }]
                formatted_messages = user_messages
            else:
                # OpenAI doesn't support caching in the same way
                formatted_messages = [msg.model_dump() for msg in messages]
                system_arg = None

            # Prepare thinking configuration if using system reasoning
            thinking_config = None
            if system_reasoning:
                thinking_config = {
                    "type": "enabled",
                    "budget_tokens": 2000
                }

            # Estimate token usage for rate limiting (conservative estimate)
            estimated_input_tokens = sum(len(str(msg.get('content', ''))) for msg in formatted_messages) // 4
            if system_content:
                estimated_input_tokens += len(system_content) // 4

            estimated_tokens = estimated_input_tokens + min(max_tokens, 4000)  # Most responses won't use max tokens

            # Wait for rate limit capacity
            if model.provider.provider == AIProviderName.anthropic:
                await rate_limiter.wait_for_capacity(estimated_tokens)

            # Create a new aggregator and usage tracker for each attempt
            aggregator = StreamingJSONAggregator(on_complete_json)
            usage_tracker = StreamingUsageTracker()

            # Setup for collecting the result
            result = ""
            current_block_type = None
            current_content = ""

            # If this is a retry, log it
            if retry_count > 0:
                retry_msg = f"Retry {retry_count}/{max_retries} with max_tokens={max_tokens}"
                print(retry_msg)
                if on_chunk:
                    on_chunk(f"\n--- {retry_msg} ---\n")

            # Stream the response
            kwargs = {
                "model": model.systemName,
                "temperature": temperature,
                "messages": formatted_messages,
                "max_tokens": max_tokens
            }

            if model.provider.provider == AIProviderName.anthropic:
                if system:
                    kwargs["system"] = system

                if thinking_config:
                    kwargs["thinking"] = thinking_config

            elif model.provider.provider == AIProviderName.open_ai:
                # Enable usage tracking for OpenAI if needed
                if return_usage:
                    kwargs["stream_options"] = {"include_usage": True}

            provider = model.provider.provider

            if provider == AIProviderName.anthropic:
                # With Anthropic, we need async with to manage the stream
                async with client.messages.stream(**kwargs) as stream:
                    await process_stream(stream, system_reasoning, usage_tracker)

            elif provider == AIProviderName.open_ai:
                # With OpenAI, we don't need async with, just await the stream
                open_ai_client: AsyncOpenAI = client
                stream = await open_ai_client.chat.completions.create(
                    stream=True, **kwargs
                )
                await process_stream(stream, system_reasoning, usage_tracker)

            # Get token usage from the completed stream to update our rate limiter
            rate_limiter.record_usage(estimated_tokens)

            # Determine final content
            final_content = None

            # Return the complete JSON, or try to parse the entire buffer if we didn't find complete JSON
            if parse_json and aggregator.complete_json:
                final_content = aggregator.complete_json
            elif parse_json:
                # Try to parse the full response as JSON
                def parse_thoroughly(text):
                    original_text = text
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        print(f"Failed to parse a valid JSON from the response: {text[:100]}...")
                        try:
                            text = fix_invalid_json(text)
                            return json.loads(text)
                        except json.JSONDecodeError:
                            print(f"Failed to parse after fix_invalid_json.")
                            try:
                                text = fix_invalid_boolean_and_null(text)
                                return json.loads(text)
                            except Exception as ex:
                                print(f"Failed to parse after fix_invalid_boolean_and_null.")
                                print(traceback.format_exc())
                                return original_text

                try:
                    content = aggregator.get_content()
                    final_content = parse_thoroughly(content)
                except json.JSONDecodeError:
                    print(f"Failed to parse a valid JSON from the response: {content[:100]}...")
                    # Return raw content instead of raising an exception
                    final_content = aggregator.get_content()
            else:
                final_content = aggregator.get_content()

            # Return based on whether usage is requested
            if return_usage:
                return final_content, usage_tracker.usage, usage_tracker.provider_message_id
            else:
                return final_content

        except Exception as e:
            # Handle rate limit errors with graceful degradation
            e_str = str(e)
            if ("rate_limit_error" in str(e) or "overloaded_error" in e_str
                    or (isinstance(e, openai.BadRequestError) and "Please reduce the length of the message" in e.message)):
                retry_count += 1

                if retry_count > max_retries:
                    print(f"Maximum retries ({max_retries}) exceeded. Last error: {e}")
                    raise

                print(f"Rate limit exceeded (attempt {retry_count}/{max_retries}): {e}")

                # Implement graceful degradation of parameters
                # Calculate wait time with exponential backoff
                wait_time = 5 * (retry_backoff_factor ** (retry_count - 1))

                # Gradually reduce max_tokens with each retry (more graceful degradation)
                # First retry: 75% of original, Second: 50%, Third: 25%
                reduction_factor = 1.0 - (0.25 * retry_count)
                max_tokens = int(original_max_tokens * max(0.25, reduction_factor))

                print(f"Waiting {wait_time:.1f} seconds, reducing max_tokens to {max_tokens}")
                if on_chunk:
                    on_chunk(f"\n--- Rate limit exceeded. Retrying in {wait_time:.1f} seconds with max_tokens={max_tokens} ---\n")

                # Wait with exponential backoff
                await asyncio.sleep(wait_time)

                # Double the estimated tokens for rate limiter to be more conservative
                await rate_limiter.wait_for_capacity(estimated_tokens * (1 + retry_count))

                # Continue to next iteration (retry)
                continue
            else:
                # For other errors, log and re-raise
                print(f"Error in llm_streaming_with_progress: {e}")
                raise

@track_llm(
    # default provider/model extractors already work (pulled from kw['model'])
    # default usage extractor also works (StreamingResultWrapper has .usage)
    metadata_extractor=_streaming_metadata_extractor
)
async def llm_streaming_structured(
    model,
    messages,
    message_id: str = "streaming_msg",
    parse_json=True,
    temperature=None,
    max_tokens=None,
    on_chunk=None,
    on_complete_json=None,
    system_reasoning=True,
    max_retries=10,
    retry_backoff_factor=2.0
) -> StreamingResultWrapper:
    """
    Alternative API that returns structured StreamingResultWrapper similar to BatchResultWrapper.

    Args:
        model: Model record containing provider details
        messages: List of messages to send
        message_id: Custom message ID for tracking
        parse_json: Whether to parse the response as JSON
        temperature: Temperature parameter for the model
        max_tokens: Maximum tokens to generate
        on_chunk: Callback for each received chunk
        on_complete_json: Callback when a complete JSON is detected
        system_reasoning: Whether to enable extended thinking mode
        max_retries: Maximum number of retry attempts for rate limit errors
        retry_backoff_factor: Exponential backoff factor for retry delays

    Returns:
        StreamingResultWrapper with structured response data
    """

    wrapper = StreamingResultWrapper(
        message_id=message_id,
        model=model.systemName,
        provider=model.provider.provider.value if hasattr(model.provider.provider, 'value') else str(model.provider.provider)
    )

    system_reasoning = system_reasoning and (model.provider.provider == AIProviderName.open_ai or (model.provider.provider == AIProviderName.anthropic and "haiku" not in model.systemName.lower()))
    try:
        # Call the main streaming function with usage tracking
        content, usage, provider_message_id = await llm_streaming_with_progress(
            model=model,
            messages=messages,
            parse_json=parse_json,
            temperature=temperature,
            max_tokens=max_tokens,
            on_chunk=on_chunk,
            on_complete_json=on_complete_json,
            system_reasoning=system_reasoning,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
            return_usage=True  # Request usage information
        )

        # Success case
        wrapper.success = True
        wrapper.provider_message_id = provider_message_id
        wrapper.usage = usage

        if (isinstance(content, dict) or isinstance(content, list)) and parse_json:
            # Convert JSON response to string for consistency
            wrapper.content = json.dumps(content, indent=2, ensure_ascii=False)
        else:
            wrapper.content = str(content)

    except Exception as e:
        # Error case
        wrapper.success = False
        wrapper.error_message = str(e)
        wrapper.content = ""
        wrapper.usage = TokenUsage()  # Empty usage on error

    return wrapper


# Example usage for progress reporting:
async def example_with_progress(model_record):

    system_message = Message(
        role="system",
        content="You are a helpful data engineering assistant that provides answers in JSON format.",
        cache_strategy="ephemeral"
    )
    user_message = Message(
        role="user",
        content="Generate a JSON with 2 data engineering interview questions and answers."
    )

    def on_chunk(chunk):
        # Print chunk in real-time
        print(chunk, end="", flush=True)

    def on_complete_json(json_obj):
        print(f"\n\nComplete JSON detected early! Processing can begin...\n{json_obj}")
        # You could start processing the JSON before the stream completes

    # # Example 1: Original API
    # print("=== Original API ===")
    # result = await llm_streaming_with_progress(
    #     model=model_record,
    #     messages=[system_message, user_message],
    #     parse_json=True,
    #     on_chunk=on_chunk,
    #     on_complete_json=on_complete_json,
    #     max_tokens=2001
    # )
    #
    # print("\n\nFinal result:", result)
    #
    # # Example 2: Original API with usage
    # print("\n\n=== Original API with Usage ===")
    # content, usage, provider_id = await llm_streaming_with_progress(
    #     model=model_record,
    #     messages=[system_message, user_message],
    #     parse_json=True,
    #     on_chunk=on_chunk,
    #     on_complete_json=on_complete_json,
    #     max_tokens=2001,
    #     return_usage=True
    # )
    #
    # print(f"\n\nContent: {content}")
    # print(f"Usage: {usage}")
    # print(f"Provider Message ID: {provider_id}")

    # Example 3: New structured API
    print("\n\n=== New Structured API ===")
    wrapper_result = await llm_streaming_structured(
        model=model_record,
        messages=[system_message, user_message],
        message_id="test_streaming_1",
        parse_json=True,
        on_chunk=on_chunk,
        on_complete_json=on_complete_json,
        max_tokens=2001
    )

    print("\n\nStructured result:")
    print(f"Success: {wrapper_result.success}")
    print(f"Message ID: {wrapper_result.message_id}")
    print(f"Provider Message ID: {wrapper_result.provider_message_id}")
    print(f"Model: {wrapper_result.model}")
    print(f"Provider: {wrapper_result.provider}")

    if wrapper_result.usage:
        print(f"Usage: Input={wrapper_result.usage.input_tokens}, Output={wrapper_result.usage.output_tokens}, "
              f"Cache Read={wrapper_result.usage.cache_read_tokens}, Cache Creation={wrapper_result.usage.cache_creation_tokens}, "
              f"Total={wrapper_result.usage.total_tokens}")

    if wrapper_result.has_error:
        print(f"Error: {wrapper_result.error_message}")
    else:
        print(f"Content length: {len(wrapper_result.content)} characters")
        print(f"Content preview: {wrapper_result.content[:200]}...")

    # Convert to dict format like BatchResultWrapper
    result_dict = wrapper_result.to_dict()
    print("\nAs dictionary:")
    print(json.dumps(result_dict, indent=2, ensure_ascii=False))

def test_platform_streaming():
    import os
    project = os.environ.get("DEFAULT_PROJECT_NAME", None)
    tenant = os.environ.get("DEFAULT_TENANT", None)

    provider = AIProviderName.open_ai
    model_record_openai = ModelRecord(
        systemName="gpt-4o-mini",
        provider=AIProvider(
            provider=provider,
            apiToken=os.getenv("OPENAI_API_KEY"),
        ),
    )

    provider = AIProviderName.anthropic
    model_record_anthropic = ModelRecord(
        systemName="claude-3-5-haiku-20241022",
        provider=AIProvider(
            provider=provider,
            apiToken=os.getenv("ANTHROPIC_API_KEY"),
        ),
    )

    KDCUBE_STORAGE_PATH = os.environ.get("KDCUBE_STORAGE_PATH",
                                         "file:///Users/elenaviter/src/third/aib/crew-ai/benchmark/data/kdcube")
    STORAGE_KWARGS = {}  # or AWS creds for S3
    kdcube_storage_backend = create_storage_backend(KDCUBE_STORAGE_PATH, **STORAGE_KWARGS)


    AccountingSystem.init_storage(
        storage_backend=kdcube_storage_backend,
        # optional: custom path strategy; default is grouped_by_component_and_seed()
    )

    # 2) set a minimal context (like a request would via your auth dependency)
    AccountingSystem.set_context(
        user_id="dev-user",
        session_id="dev-session",
        tenant_id=tenant,
        project_id=project,
        request_id="dev-req-1",
        component="dev.shell"  # a default; can be overridden in with_component scopes
    )
    with with_accounting("dev.shell",
                         metadata={"purpose": "test"}):
        # model_record = model_record_anthropic
        model_record = model_record_openai
        asyncio.run(example_with_progress(model_record=model_record))
        print()

if __name__ == "__main__":
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())
    # test_platform_streaming()
