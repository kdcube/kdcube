# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import time, random
from kdcube_ai_app.infra.llm.llm_data_model import AIProviderName, Message
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import asyncio
import logging

async def get_service_key_fn(provider: AIProviderName) -> str:
    """
    Get the API key for the given provider.
    """
    from kdcube_ai_app.apps.chat.sdk.config import get_secret

    if provider == AIProviderName.open_ai:
        return await get_secret("services.openai.api_key", default="") or ""
    if provider == AIProviderName.hugging_face:
        return await get_secret("services.huggingface.api_key", default="") or ""
    if provider == AIProviderName.anthropic:
        return await get_secret("services.anthropic.api_key", default="") or ""
    if provider == AIProviderName.open_router:
        return await get_secret("services.openrouter.api_key", default="") or ""
    return ""


def calculate_cost(prefix_tokens: int, suffix_tokens: list[int], output_tokens: list[int],
                   cost_in: float, cost_out: float) -> float:
    """
    Calculate the total cost based on the formula:

      E = ((P + sum(S_i)) / 1e6) * cost_in + (sum(O_i) / 1e6) * cost_out

    Args:
        prefix_tokens (int): The static prefix token count (P). In a cached scenario,
                               this is counted only once.
        suffix_tokens (list[int]): A list where each element is the dynamic (suffix) token count (S_i) for a request.
        output_tokens (list[int]): A list where each element is the output token count (O_i) per response.
        cost_in (float): Input cost per million tokens.
        cost_out (float): Output cost per million tokens.

    Returns:
        float: The total cost in dollars.
    """
    total_suffix_tokens = sum(suffix_tokens)
    total_output_tokens = sum(output_tokens)
    total_input_tokens = prefix_tokens + total_suffix_tokens

    cost = (total_input_tokens / 1_000_000) * cost_in + (total_output_tokens / 1_000_000) * cost_out
    return cost

def calculate_tokens(text: str) -> int:
    """
    Calculate an approximate token count for a list of messages.

    This uses a simple approximation: assume ~1 token per 4 characters in the message content.
    Note: For more accurate token counting, consider using a tokenizer tailored for your LLM model.

    Args:
        messages: A list of Message objects.

    Returns:
        The total estimated token count.
    """
    return len(text) // 4 if text else 0

def retry_with_exponential_backoff(
        func,
        initial_delay: float = 1,
        exponential_base: float = 2,
        jitter: bool = True,
        max_retries: int = 10,
        errors: tuple = (Exception,)
):
    """Retry a function with exponential backoff."""
    def wrapper(*args, **kwargs):
        num_retries = 0
        delay = initial_delay
        while True:
            try:
                return func(*args, **kwargs)
            except errors as e:
                num_retries += 1
                if num_retries > max_retries:
                    raise Exception(f"Maximum number of retries ({max_retries}) exceeded.") from e
                # Apply exponential backoff (with optional jitter)
                if jitter:
                    delay = delay * exponential_base * (1 + random.random())
                else:
                    delay *= exponential_base
                print(f"Retry {num_retries}/{max_retries} in {delay:.2f} seconds due to error: {e}")
                time.sleep(delay)
    return wrapper

def convert_messages_for_anthropic(messages: List[Message]) -> List[Dict[str, str]]:
    """Convert our Message objects to the format expected by Anthropic's API."""
    converted_messages = []

    for message in messages:
        # Convert to Anthropic's expected format
        # Anthropic doesn't use 'developer' role, so treat it as 'user'
        role = message.role
        if role == "developer":
            role = "user"

        # For system message, Anthropic expects it to be handled differently
        if role == "system":
            continue  # We'll handle the system message separately

        converted_messages.append({
            "role": role,
            "content": message.content
        })

    return converted_messages

def get_system_message(messages: List[Message]) -> Optional[str]:
    """Extract the system message from the messages list."""
    for message in messages:
        if message.role == "system":
            return message.content
    return None

class TokenRateLimiter:
    """
    Manages token rate limits for Anthropic API.
    Prevents 429 errors by tracking token usage and waiting when necessary.
    """

    def __init__(
            self,
            tokens_per_minute: int = 8000,
            safety_buffer: float = 0.9,  # Use 90% of the limit to be safe
            window_size: int = 60  # Window size in seconds (1 minute)
    ):
        """
        Initialize the token rate limiter.

        Args:
            tokens_per_minute: The rate limit (tokens per minute)
            safety_buffer: Multiplier to apply to limit (0.9 = use 90% of limit)
            window_size: Time window in seconds
        """
        self.token_limit = tokens_per_minute * safety_buffer
        self.window_size = window_size
        self.usage_history = []
        self.lock = asyncio.Lock()
        self.logger = logging.getLogger("TokenRateLimiter")

        # Initialize logger if not already configured
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    async def wait_for_capacity(self, requested_tokens: float) -> None:
        """
        Wait until there is capacity to process the requested tokens.

        Args:
            requested_tokens: Number of tokens the upcoming request will use
        """
        async with self.lock:
            now = time.time()

            # Clean up old usage records
            self.usage_history = [
                usage for usage in self.usage_history
                if now - usage["timestamp"] < self.window_size
            ]

            # Calculate current usage in the window
            current_usage = sum(usage["tokens"] for usage in self.usage_history)

            # Calculate how many more tokens we can use
            available_capacity = int(max(0.0, self.token_limit - current_usage))

            if requested_tokens > self.token_limit:
                self.logger.warning(
                    f"Request for {requested_tokens} tokens exceeds even the total limit of {self.token_limit}. "
                    f"This request will likely fail with a rate limit error."
                )

            # If we don't have enough capacity, we need to wait
            if requested_tokens > available_capacity:
                # Calculate how long we need to wait
                # Find the oldest usage record that we need to wait for
                tokens_to_free = requested_tokens - available_capacity
                tokens_freed = 0
                wait_until = now

                for usage in sorted(self.usage_history, key=lambda x: x["timestamp"]):
                    tokens_freed += usage["tokens"]
                    if tokens_freed >= tokens_to_free:
                        # We've found enough tokens, calculate the time to wait
                        wait_until = usage["timestamp"] + self.window_size
                        break

                wait_seconds = max(0, wait_until - now)

                if wait_seconds > 0:
                    expiry_time = datetime.now() + timedelta(seconds=wait_seconds)
                    self.logger.info(
                        f"Rate limit approaching: waiting {wait_seconds:.2f} seconds "
                        f"until {expiry_time.strftime('%H:%M:%S')} "
                        f"(need {tokens_to_free} tokens, current usage: {current_usage}/{self.token_limit:.0f})"
                    )
                    await asyncio.sleep(wait_seconds)

            # Record this usage for future calculations
            self.usage_history.append({
                "timestamp": time.time(),
                "tokens": requested_tokens
            })

    def record_usage(self, tokens_used: int) -> None:
        """
        Record actual token usage after a request completes.
        This is more accurate than the estimated usage.

        Args:
            tokens_used: Actual tokens used in the last request
        """
        # Remove the last recorded (estimated) usage
        if self.usage_history:
            self.usage_history.pop()

        # Add the actual usage
        self.usage_history.append({
            "timestamp": time.time(),
            "tokens": tokens_used
        })

    async def extract_token_usage_from_headers(self, headers: Dict[str, Any]) -> Dict[str, int]:
        """
        Extract token usage information from Anthropic API response headers.

        Args:
            headers: Response headers from Anthropic API

        Returns:
            Dictionary with token usage information
        """
        usage = {}

        # Extract relevant headers
        try:
            if 'x-ratelimit-tokens-remaining' in headers:
                usage['tokens_remaining'] = int(headers['x-ratelimit-tokens-remaining'])

            if 'x-ratelimit-tokens-limit' in headers:
                usage['tokens_limit'] = int(headers['x-ratelimit-tokens-limit'])

            if 'x-ratelimit-tokens-reset' in headers:
                usage['tokens_reset'] = int(headers['x-ratelimit-tokens-reset'])

            if 'x-ratelimit-requests-remaining' in headers:
                usage['requests_remaining'] = int(headers['x-ratelimit-requests-remaining'])

            if 'x-ratelimit-requests-limit' in headers:
                usage['requests_limit'] = int(headers['x-ratelimit-requests-limit'])
        except (ValueError, KeyError) as e:
            self.logger.warning(f"Error parsing rate limit headers: {e}")

        return usage


# Singleton instance for use across the application
rate_limiter = TokenRateLimiter()


async def rate_limited_request(
        client_func,
        estimated_tokens: int = 1000,
        *args,
        **kwargs
):
    """
    Execute an API request with rate limiting.

    Args:
        client_func: The client function to call (e.g., client.messages.create)
        estimated_tokens: Estimated tokens the request will use
        *args, **kwargs: Arguments to pass to the client function

    Returns:
        The result of the client function
    """
    # Wait for capacity before making the request
    await rate_limiter.wait_for_capacity(estimated_tokens)

    # Make the request
    try:
        response = await client_func(*args, **kwargs)

        # If there are headers with usage information, use them to update our tracker
        if hasattr(response, 'usage') and hasattr(response.usage, 'output_tokens'):
            rate_limiter.record_usage(response.usage.output_tokens)

        return response
    except Exception as e:
        # Handle rate limit errors specially
        if "rate_limit_error" in str(e):
            # If we hit a rate limit, wait longer next time
            rate_limiter.token_limit *= 0.8  # Reduce our limit by 20%
            logging.error(f"Rate limit exceeded. Reducing capacity to {rate_limiter.token_limit:.0f} tokens")

            # Wait a bit before retrying
            await asyncio.sleep(5)

            # Retry the request with a longer wait
            await rate_limiter.wait_for_capacity(estimated_tokens * 1.5)
            return await client_func(*args, **kwargs)
        else:
            # Re-raise other errors
            raise

def fix_invalid_json(s):
    def is_escaped(s, index):
        """Return True if character at s[index] is preceded by an odd number of backslashes."""
        count = 0
        j = index - 1
        while j >= 0 and s[j] == '\\':
            count += 1
            j -= 1
        return count % 2 == 1

    result = []
    i = 0
    # Scan through the full string
    while i < len(s):
        # Check if we encounter a content field
        if s.startswith('"content": "', i):
            # Append the key and the opening quote
            result.append('"content": "')
            i += len('"content": "')
            # Process the content string character by character
            while i < len(s):
                c = s[i]
                if c == '\n':
                    # Replace newline with escaped newline
                    result.append('\\n')
                    i += 1
                elif c == '"':
                    if is_escaped(s, i):
                        # Already escaped; leave it
                        result.append(c)
                        i += 1
                    else:
                        # Not yet escaped; check the next character (if any)
                        next_char = s[i+1] if i + 1 < len(s) else None
                        # If next char is one of these, assume the quote is a delimiter
                        if next_char in [',', ']', '}']:
                            result.append(c)
                            i += 1
                            break  # End of this content string
                        else:
                            # Escape the quote
                            result.append('\\' + c)
                            i += 1
                else:
                    result.append(c)
                    i += 1
        else:
            # If not in a "content" value, just copy the character
            result.append(s[i])
            i += 1

    return ''.join(result)

def fix_invalid_boolean_and_null(text):
    import ast
    return ast.literal_eval(text)

def _count_tokens(tokenizer, system, question, answer):
    """Count tokens for a complete sample"""
    try:
        if hasattr(tokenizer, 'chat_template') and tokenizer.chat_template is not None:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer}
            ]
            try:
                combined_text = tokenizer.apply_chat_template(messages, tokenize=False)
                token_count = len(tokenizer.encode(combined_text))
            except:
                # Fallback for older tokenizers
                combined_text = f"{system}\n\n{question}\n\nAssistant: {answer}"
                token_count = len(tokenizer.encode(combined_text))
        else:
            combined_text = f"{system}\n\n{question}\n\nAssistant: {answer}"
            token_count = len(tokenizer.encode(combined_text))
        return token_count
    except Exception as e:
        print(f"Error counting tokens: {e}")
        return float('inf')  # To ensure it gets filtered out

def apply_cache_strategy(message):
    if message.role == "system":
        return {
            "type": "text",
            "text": message.content,
            **({"cache_control": { "type": message.cache_strategy }} if message.cache_strategy else {})
        }
    elif message.role == "user":
        return {
            "type": "text",
            "text": message.content,
            **({"cache_control": { "type": message.cache_strategy }} if message.cache_strategy else {})
        }
