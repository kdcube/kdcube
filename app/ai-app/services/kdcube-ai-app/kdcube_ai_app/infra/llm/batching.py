# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/llm/batching.py
import abc
from dataclasses import dataclass
import json

import enum
import logging
from typing import List, Optional, Dict, Any, ClassVar, Callable
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from openai import AsyncOpenAI
import anthropic
import openai

from kdcube_ai_app.infra.llm.llm_data_model import (AIProviderName, Message,
                                                    ModelRecord, AIProvider,
                                                    wrap_batch_results)
from kdcube_ai_app.infra.llm.util import apply_cache_strategy

logger = logging.getLogger(__name__)

class BatchStatus(str, enum.Enum):
    draft = "draft"
    in_progress = "in-progress"
    failed = "failed"
    succeeded = "succeeded"

@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int

@dataclass
class BatchMessage:
    def __init__(self, id, messages: List[Message], max_tokens: int = 1024) -> None:
        self.id = id
        self.messages = messages
        self.max_tokens = max_tokens
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self.usage: Optional[Usage] = None
        self.native_result: Any = None

class Batch(abc.ABC):
    def __init__(self, model: str, id: Optional[str], messages: Optional[List[BatchMessage]] = None) -> None:
        if id is not None and messages is not None:
            raise ValueError("Either id or messages should be provided, not both.")

        self.model = model
        self.id = id
        self.messages = messages
        self.status = BatchStatus.draft
        self.fail_reason = None
        self.native_obj = None

    @abc.abstractmethod
    async def update_status(self):
        raise NotImplementedError()

    @abc.abstractmethod
    async def create(self):
        raise NotImplementedError()

    @abc.abstractmethod
    async def cancel(self):
        raise NotImplementedError()

    @abc.abstractmethod
    async def delete(self):
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def size_in_bytes(self) -> int:
        """Calculate the size of the batch in bytes."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def estimate_tokens(self) -> int:
        """Estimate the number of tokens in the batch."""
        raise NotImplementedError()
        
    @staticmethod
    @abc.abstractmethod
    async def list():
        raise NotImplementedError()


class OpenAIBatch(Batch):

    __status_map = {
        "validating": BatchStatus.in_progress,
        "in_progress": BatchStatus.in_progress,
        "finalizing": BatchStatus.in_progress,
        "failed": BatchStatus.failed,
        "expired": BatchStatus.failed,
        "cancelling": BatchStatus.failed,
        "cancelled": BatchStatus.failed,
        "completed": BatchStatus.succeeded,
    }

    def __init__(self, model: str, id: Optional[str], messages: Optional[List[BatchMessage]] = None, client: Optional[AsyncOpenAI] = None) -> None:
        super().__init__(model, id, messages)
        self.client = client or AsyncOpenAI()
        self._input_file_id: Optional[str] = None  # Track file for cleanup

    async def update_status(self):
        if not self.id:
            raise ValueError("Batch ID is required to update status.")

        try:
            batch = await self.client.batches.retrieve(self.id)
            if not batch:
                raise ValueError("Batch not found.")
            self.native_obj = batch
            previous_status = self.status
            self._update_status_from_openai(batch.status)

            if self.status == BatchStatus.succeeded and previous_status != BatchStatus.succeeded:
                await self._update_message_results()
                # Clean up input file after successful completion
                await self._cleanup_input_file()

            return True
        except openai.NotFoundError:
            self.status = BatchStatus.draft
            return True
        except Exception as e:
            logger.error(f"Failed to update batch status: {e}")
            return False

    async def create(self):
        if self.status != BatchStatus.draft:
            raise ValueError("Batch already created.")
        if self.messages is None:
            raise ValueError("Messages must be provided to create a batch.")

        # Create JSONL content
        jsonl_messages = "\n".join([json.dumps(self._convert_message_to_request(m), ensure_ascii=False) for m in self.messages])

        # Use io.BytesIO which OpenAI accepts
        import io

        # Create BytesIO object with JSONL content
        file_content = io.BytesIO(jsonl_messages.encode('utf-8'))
        file_content.name = 'batch_input.jsonl'  # OpenAI might use this for reference

        try:
            # Upload file to OpenAI
            batch_input_file = await self.client.files.create(
                file=file_content,
                purpose="batch"
            )

            if not batch_input_file or not getattr(batch_input_file, "id", None):
                raise ValueError("Failed to create batch input file.")

            self._input_file_id = batch_input_file.id
            logger.info(f"Created input file: {self._input_file_id}")

            # Create batch
            self.native_obj = await self.client.batches.create(
                input_file_id=batch_input_file.id,
                endpoint="/v1/chat/completions",
                completion_window="24h",
                metadata=None
            )

            if not self.native_obj or not getattr(self.native_obj, "id", None):
                logger.error(f"Failed to create batch. native_obj = {self.native_obj}")
                raise ValueError("Failed to create a batch.")

            self.id = self.native_obj.id
            self._update_status_from_openai(self.native_obj.status)
            logger.info(f"Created batch: {self.id} with input file: {self._input_file_id}")

        except Exception as e:
            # Clean up file if batch creation failed
            if self._input_file_id:
                try:
                    await self.client.files.delete(self._input_file_id)
                    logger.info(f"Cleaned up failed batch input file: {self._input_file_id}")
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup input file: {cleanup_error}")
            raise e

    async def cancel(self):
        """Cancel an in-progress batch."""
        if not self.id:
            raise ValueError("Batch ID is required to cancel.")

        try:
            self.native_obj = await self.client.batches.cancel(self.id)
            self._update_status_from_openai(self.native_obj.status)
            # Clean up input file after cancellation
            await self._cleanup_input_file()
            return True
        except Exception as e:
            logger.error(f"Failed to cancel batch: {e}")
            return False

    async def delete(self):
        """
        OpenAI doesn't support direct batch deletion.
        This method will cancel the batch if it's in progress and clean up files.
        """
        if not self.id:
            raise ValueError("Batch ID is required to delete.")

        # If the batch is in progress, cancel it first
        if self.status == BatchStatus.in_progress:
            success = await self.cancel()
            if not success:
                return False
        else:
            # Clean up input file even if not cancelling
            await self._cleanup_input_file()

        logger.info(f"Batch {self.id} marked as deleted")
        return True

    async def _cleanup_input_file(self):
        """Clean up the input file after batch completion/cancellation."""
        if self._input_file_id:
            try:
                await self.client.files.delete(self._input_file_id)
                logger.info(f"Cleaned up input file: {self._input_file_id}")
                self._input_file_id = None
            except Exception as e:
                logger.warning(f"Failed to cleanup input file {self._input_file_id}: {e}")

    @property
    def size_in_bytes(self) -> int:
        """Calculate the size of the OpenAI batch in bytes."""
        if not self.messages:
            return 0

        total_size = 0
        for message in self.messages:
            # Sum up the size of each message
            for msg in message.messages:
                # Count the content bytes for each message
                total_size += len(msg.content.encode('utf-8'))

        return total_size

    async def estimate_tokens(self) -> int:
        """Estimate the number of tokens in the batch using tiktoken."""
        if not self.messages:
            return 0

        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model(self.model)
        except (ImportError, KeyError):
            # If tiktoken is not installed or model not found, fall back to rough estimate
            logger.warning(f"Tiktoken not available or model {self.model} not found, using rough estimate")
            # Rough estimate: 1 token ≈ 4 bytes
            return self.size_in_bytes // 4

        total_tokens = 0
        for batch_message in self.messages:
            message_tokens = 0
            for message in batch_message.messages:
                # Encode the content to get token count
                message_tokens += len(encoding.encode(message.content))
                # Add a small number for role and message formatting
                message_tokens += 4  # Approximation for role and formatting

            total_tokens += message_tokens

        return total_tokens

    async def _update_message_results(self):
        """Update results for all messages in the batch after completion."""
        if not self.native_obj or not self.native_obj.output_file_id:
            logger.warning("No output file ID available for completed batch")
            return

        try:
            file_response = await self.client.files.content(self.native_obj.output_file_id)
            content = file_response.read()

            # If messages are None, create them from the results
            if self.messages is None:
                self.messages = []
                message_map = {}
            else:
                message_map = {m.id: m for m in self.messages}

            for line in content.decode('utf-8').splitlines():
                if not line.strip():
                    continue

                result = json.loads(line)
                custom_id = result.get('custom_id')

                if custom_id in message_map:
                    batch_message = message_map[custom_id]

                    # Extract result properly
                    if 'response' in result and result['response']:
                        response = result['response']
                        if 'body' in response and 'choices' in response['body']:
                            choices = response['body']['choices']
                            if choices and len(choices) > 0:
                                batch_message.result = choices[0]['message']['content']

                        # Extract usage if available
                        if 'body' in response and 'usage' in response['body']:
                            usage_data = response['body']['usage']
                            batch_message.usage = Usage(
                                input_tokens=usage_data.get('prompt_tokens', 0),
                                output_tokens=usage_data.get('completion_tokens', 0),
                                cache_creation_input_tokens=0,  # OpenAI doesn't provide this
                                cache_read_input_tokens=0  # OpenAI doesn't provide this
                            )

                    batch_message.native_result = result

                elif self.messages is not None:
                    # Create a new BatchMessage for this result
                    new_message = BatchMessage(id=custom_id, messages=[], max_tokens=0)

                    if 'response' in result and result['response']:
                        response = result['response']
                        if 'body' in response and 'choices' in response['body']:
                            choices = response['body']['choices']
                            if choices and len(choices) > 0:
                                new_message.result = choices[0]['message']['content']

                    new_message.native_result = result
                    self.messages.append(new_message)
                    message_map[custom_id] = new_message

            # Handle errors
            if self.native_obj.error_file_id:
                error_response = await self.client.files.content(self.native_obj.error_file_id)
                error_content = error_response.read()

                for line in error_content.decode('utf-8').splitlines():
                    if not line.strip():
                        continue

                    error = json.loads(line)
                    custom_id = error.get('custom_id')

                    if custom_id in message_map:
                        message_map[custom_id].error = error.get('error', {}).get('message', 'Unknown error')

        except Exception as e:
            logger.error(f"Failed to update message results: {e}")

    async def start_polling(self, interval_seconds: float = 10.0,
                            timeout_seconds: Optional[float] = None,
                            callback: Optional[Callable[[str], None]] = None):
        """
        Start polling for batch status updates until the batch completes or fails.

        Args:
            interval_seconds: Time between status checks in seconds
            timeout_seconds: Maximum polling time in seconds (None for no timeout)
            callback: Optional callback function that receives the current status

        Returns:
            The final BatchStatus
        """
        if not self.id:
            raise ValueError("Batch ID is required to start polling.")

        start_time = asyncio.get_event_loop().time()

        while True:
            # Check if we've exceeded the timeout
            if timeout_seconds is not None:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    logger.warning(f"Polling timed out after {elapsed:.1f} seconds")
                    return self.status

            # Update batch status
            success = await self.update_status()
            if not success:
                logger.error("Failed to update batch status during polling")
                return self.status

            # Call the callback if provided
            if callback:
                try:
                    callback(self.status)
                except Exception as e:
                    logger.error(f"Error in status callback: {e}")

            # Check if we're done
            if self.status in [BatchStatus.succeeded, BatchStatus.failed]:
                return self.status

            # Wait before checking again
            await asyncio.sleep(interval_seconds)

    @staticmethod
    async def list(client: Optional[AsyncOpenAI] = None, limit: int = 100):
        """List all batches."""
        client = client or AsyncOpenAI()

        try:
            batches = await client.batches.list(limit=limit)
            return batches
        except Exception as e:
            logger.error(f"Failed to list batches: {e}")
            return []

    def _update_status_from_openai(self, openai_status: str) -> None:
        if openai_status in self.__status_map:
            self.status = self.__status_map[openai_status]
            if openai_status == "failed":
                self.fail_reason = getattr(self.native_obj, 'fail_reason', "Batch failed")
            elif openai_status == "expired":
                self.fail_reason = "Batch expired"
            elif openai_status == "cancelled" or openai_status == "cancelling":
                self.fail_reason = "Batch cancelled"
        else:
            raise ValueError(f"Unknown batch status: {openai_status}")

    def _convert_message_to_request(self, message: BatchMessage):
        # Convert messages to OpenAI format
        openai_messages = []
        for msg in message.messages:
            openai_messages.append({
                "role": msg.role,
                "content": msg.content
            })

        return {
            "custom_id": message.id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": self.model,
                "messages": openai_messages,
                "max_tokens": message.max_tokens,
            }
        }

class AnthropicBatch(Batch):
    __status_map: ClassVar[Dict[str, BatchStatus]] = {
        "in_progress": BatchStatus.in_progress,
        "ended": BatchStatus.succeeded,
    }

    def __init__(self, model: str, id: Optional[str], messages: Optional[List[BatchMessage]] = None, client: Optional[anthropic.AsyncAnthropic] = None) -> None:
        super().__init__(model, id, messages)
        self.client = client or anthropic.AsyncAnthropic()
    
    async def update_status(self):
        """Update the status of the batch."""
        if not self.id:
            raise ValueError("Batch ID is required to update status.")

        try:
            batch = await self.client.messages.batches.retrieve(self.id)
            if not batch:
                raise ValueError("Batch not found.")
            
            self.native_obj = batch
            previous_status = self.status
            self._update_status_from_anthropic(batch.processing_status)
            
            if self.status == BatchStatus.succeeded and previous_status != BatchStatus.succeeded:
                await self._update_message_results()
            return True
        except Exception as e:
            logger.error(f"Failed to update batch status: {e}")
            return False
    
    async def create(self):
        """Create a new batch with the Anthropic API."""
        if self.status != BatchStatus.draft:
            raise ValueError("Batch already created.")
        if self.messages is None:
            raise ValueError("Messages must be provided to create a batch.")
        
        try:
            requests = [self._convert_message_to_request(message_batch=message) for message in self.messages]
            
            self.native_obj = await self.client.messages.batches.create(
                requests=requests
            )
            
            if not self.native_obj or not getattr(self.native_obj, "id", None):
                logger.error(f"Failed to create batch. native_obj = {self.native_obj}")
                raise ValueError("Failed to create a batch.")
            
            self.id = self.native_obj.id
            self._update_status_from_anthropic(self.native_obj.processing_status)
            return True
        except Exception as e:
            logger.error(f"Failed to create batch: {e}")
            raise
    
    async def cancel(self):
        """Cancel an in-progress batch."""
        # Note: As of the documentation provided, Anthropic doesn't seem to have a direct batch cancellation API
        # We'll implement a placeholder and log a warning
        if not self.id:
            raise ValueError("Batch ID is required to cancel.")
        
        logger.warning("Anthropic Batch API doesn't support direct cancellation. The batch will continue processing.")
        return False
    
    async def delete(self):
        """Delete a batch."""
        # As with cancel, Anthropic doesn't seem to have a direct batch deletion API
        # We'll implement a placeholder for consistency
        if not self.id:
            raise ValueError("Batch ID is required to delete.")
        
        logger.warning("Anthropic Batch API doesn't support direct deletion. The batch will remain in the system.")
        return False

    @property
    def size_in_bytes(self) -> int:
        """Calculate the size of the Anthropic batch in bytes."""
        if not self.messages:
            return 0
        
        total_size = 0
        for message in self.messages:
            # Sum up the size of each message
            for msg in message.messages:
                # Count the content bytes for each message
                total_size += len(msg.content.encode('utf-8'))
        
        return total_size

    async def estimate_tokens(self) -> int:
        """Estimate the number of tokens in the batch using Anthropic's token counting API."""
        if not self.messages:
            return 0
        
        try:
            total_tokens = 0
            for batch_message in self.messages:
                # Extract system content from system messages
                system_content = "\n".join([
                    m.content for m in batch_message.messages if m.role == "system"
                ])
                
                # Convert non-system messages to the format expected by count_tokens
                anthro_messages = []
                for m in batch_message.messages:
                    if m.role != "system":
                        anthro_messages.append({
                            "role": m.role,
                            "content": m.content
                        })
                
                # Call Anthropic's token counting API
                response = await self.client.messages.count_tokens(
                    model=self.model,
                    system=system_content if system_content else anthropic.NOT_GIVEN,
                    messages=anthro_messages
                )

                # TODO: Rate limit handling
                
                # Extract token count from response
                try:
                    total_tokens += response.input_tokens
                except (AttributeError, TypeError) as e:
                    logger.warning(f"Unexpected response format from Anthropic API: {e}")
                    return -1
            
            return total_tokens
        except Exception as e:
            logger.warning(f"Error estimating tokens with Anthropic API: {e}")
            return -1

    async def _update_message_results(self):
        """Update results for all messages in the batch after completion."""
        if not self.native_obj or not self.native_obj.results_url:
            logger.warning("No results URL available for completed batch")
            return
        
        try:
            # If messages are None, initialize an empty list
            if self.messages is None:
                self.messages = []
                message_map = {}
            else:
                # Get a message ID to BatchMessage mapping for easy lookup
                message_map = {m.id: m for m in self.messages}
            
            # Process results
            res_iter = await self.client.messages.batches.results(self.id)
            async for result in res_iter:
                custom_id = result.custom_id

                if custom_id in message_map:
                    if result.result.type == "succeeded":
                        message_map[custom_id].result = result.result
                    elif result.result.type == "errored":
                        message_map[custom_id].error = result.result.error
                    elif result.result.type == "expired":
                        message_map[custom_id].error = "expired"
                else:
                    # Create a new BatchMessage for this result if we don't have it
                    new_message = BatchMessage(id=custom_id, messages=[], max_tokens=0)
                    
                    if result.result.type == "succeeded":
                        new_message.native_result = result.result

                        msg = result.result.message
                        content = msg.content
                        text_content = None
                        if isinstance(content, list) and len(content) > 0:
                            text_content = ''.join([item.text for item in content if item.type == 'text'])
                        new_message.result = text_content
                        new_message.usage = Usage(
                            input_tokens=msg.usage.input_tokens,
                            output_tokens=msg.usage.output_tokens,
                            cache_creation_input_tokens=msg.usage.cache_creation_input_tokens or 0,
                            cache_read_input_tokens=msg.usage.cache_read_input_tokens or 0
                        )
                    elif result.result.type == "errored":
                        new_message.error = result.result.error.to_json()
                    elif result.result.type == "expired":
                        new_message.error = "expired"
                    
                    self.messages.append(new_message)
                    message_map[custom_id] = new_message
        except Exception as e:
            logger.error(f"Failed to update message results: {e}")
    
    async def start_polling(self, interval_seconds: float = 10.0, 
                         timeout_seconds: Optional[float] = None,
                         callback: Optional[Callable[[str], None]] = None):
        """
        Start polling for batch status updates until the batch completes or fails.
        
        Args:
            interval_seconds: Time between status checks in seconds
            timeout_seconds: Maximum polling time in seconds (None for no timeout)
            callback: Optional callback function that receives the current status
            
        Returns:
            The final BatchStatus
        """
        if not self.id:
            raise ValueError("Batch ID is required to start polling.")
        
        start_time = asyncio.get_event_loop().time()
        
        while True:
            # Check if we've exceeded the timeout
            if timeout_seconds is not None:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    logger.warning(f"Polling timed out after {elapsed:.1f} seconds")
                    return self.status
            
            # Update batch status
            success = await self.update_status()
            if not success:
                logger.error("Failed to update batch status during polling")
                return self.status
            
            # Call the callback if provided
            if callback:
                try:
                    callback(self.status)
                except Exception as e:
                    logger.error(f"Error in status callback: {e}")
            
            # Check if we're done
            if self.status in [BatchStatus.succeeded, BatchStatus.failed]:
                return self.status
            
            # Wait before checking again
            await asyncio.sleep(interval_seconds)

    
    @staticmethod
    async def list(client: Optional[anthropic.AsyncAnthropic] = None, limit: int = 100):
        """List all batches."""
        client = client or anthropic.AsyncAnthropic()
        
        try:
            # Note: The exact API call for listing batches may need to be confirmed with Anthropic documentation
            # This is based on the typical pattern but may need adjustment
            batches = await client.messages.batches.list(limit=limit)
            return batches
        except Exception as e:
            logger.error(f"Failed to list batches: {e}")
            return []
    
    def _update_status_from_anthropic(self, anthropic_status: str) -> None:
        """Map Anthropic batch status to our internal status."""
        if anthropic_status in self.__status_map:
            self.status = self.__status_map[anthropic_status]
            if anthropic_status == "ended":
                # Check if there were any errors, we would need to look at the request_counts
                if hasattr(self.native_obj, "request_counts") and self.native_obj.request_counts:
                    if getattr(self.native_obj.request_counts, "errored", 0) > 0:
                        self.status = BatchStatus.failed
                        self.fail_reason = "Some requests had errors"
        else:
            raise ValueError(f"Unknown batch status: {anthropic_status}")

    def _convert_message_to_request(self,
                                    message_batch: BatchMessage) -> Request:
        """Convert a BatchMessage to an Anthropic batch request format."""

        system = [apply_cache_strategy(message) for message in message_batch.messages if message.role == "system"]
        # in fact, this must be windowed processing of the groups of the messages originated from the same turn
        user_messages = [{
            "content": [apply_cache_strategy(message) for message in message_batch.messages if message.role != "system"],
            "role": "user"
        }]
        return Request(
            custom_id=message_batch.id,
            params=MessageCreateParamsNonStreaming(
                model=self.model,
                max_tokens=message_batch.max_tokens,
                system=system,
                messages=user_messages,
            )
        )

async def create_client(model_record: ModelRecord):
    """Create the appropriate client based on the model provider."""
    if model_record.provider.provider == AIProviderName.anthropic:
        client = anthropic.AsyncAnthropic(
            api_key=model_record.provider.apiToken,
            default_headers={
                "anthropic-beta": "output-128k-2025-02-19"
            }
        )
    elif model_record.provider.provider == AIProviderName.open_ai:
        client = AsyncOpenAI(api_key=model_record.provider.apiToken)
    else:
        raise ValueError(f"Unsupported AI provider: {model_record.provider.provider}")
    return client

async def create_batch(model_record: ModelRecord, batch_id: Optional[str] = None, messages: Optional[List[BatchMessage]] = None):
    """Factory method to create the appropriate batch instance based on the provider."""
    client = await create_client(model_record)
    
    if model_record.provider.provider == AIProviderName.anthropic:
        return AnthropicBatch(
            model=model_record.systemName,
            id=batch_id,
            messages=messages,
            client=client
        )
    elif model_record.provider.provider == AIProviderName.open_ai:
        return OpenAIBatch(
            model=model_record.systemName,
            id=batch_id,
            messages=messages,
            client=client
        )
    else:
        raise ValueError(f"Unsupported AI provider: {model_record.provider.provider}")

async def get_batch(model_record, batch_id):

    import os

    batch = await create_batch(model_record, batch_id=batch_id)

    # Update batch status
    await batch.update_status()

    wrapped_results = []
    if batch.messages:

        wrapped_results = wrap_batch_results(batch.messages, model_record.provider.provider.lower())
        print(f"\n🎯 Clean Results:")
        for i, result in enumerate(wrapped_results, 1):
            print(f"  Message {i} (ID: {result.message_id}):")
            print(f"    ✅ Success: {result.success}")
            print(f"    💬 Text: {result.text_content}")
            print(f"    🤖 Model: {result.model}")
            print(f"    🏢 Provider: {result.provider}")

            if result.usage:
                print(f"    🔢 Tokens: {result.usage.input_tokens} in + {result.usage.output_tokens} out = {result.usage.total_tokens} total")
                if result.usage.cache_read_tokens > 0:
                    print(f"    💾 Cache: {result.usage.cache_read_tokens} tokens read from cache")

            if result.has_error:
                print(f"    ❌ Error: {result.error_message}")

            # Show content blocks details if needed
            if len(result.content_blocks) > 1:
                print(f"    📄 Content blocks: {len(result.content_blocks)}")
                for j, block in enumerate(result.content_blocks):
                    print(f"      Block {j+1}: {block.type.value} - {block.content[:50]}...")

            print()
    else:
        print("No messages in the batch.")

    successful = sum(1 for r in wrapped_results if r.success)
    total_tokens = sum(r.usage.total_tokens for r in wrapped_results if r.usage)
    print(f"📊 Summary: {successful}/{len(wrapped_results)} successful, {total_tokens} total tokens")

async def try_batch():
    import os
    messages = [
        BatchMessage(id="msg1", messages=[Message(role="user", content="Hello")]),
        BatchMessage(id="msg2", messages=[Message(role="user", content="What is the capital of France?")]),
        BatchMessage(id="msg3", messages=[Message(role="system", content="You are a pirate, who always say yo-ho-ho",
                                                  cache_strategy="ephemeral"),
                                          Message(role="user", content="What is the capital of France?")]),
    ]

    # Fixed OpenAI model record
    model_record_openai = ModelRecord(
        systemName="gpt-4o-mini",
        provider=AIProvider(
            provider=AIProviderName.open_ai,
            apiToken=os.getenv("OPENAI_API_KEY"),
        ),
    )

    # Fixed Anthropic model record
    model_record_anthropic = ModelRecord(
        systemName="claude-3-5-haiku-20241022",
        provider=AIProvider(
            provider=AIProviderName.anthropic,
            apiToken=os.getenv("ANTHROPIC_API_KEY"),
        ),
    )

    async def process(model_record, provider_name):
        print(f"\n=== Testing {provider_name} ===")

        try:
            batch = await create_batch(
                model_record=model_record,
                messages=messages,
            )

            print(f"Batch created with model: {batch.model}")
            print(f"Initial status: {batch.status}")
            print(f"Messages count: {len(batch.messages) if batch.messages else 0}")
            print(f"Native obj: {batch.native_obj}")

            print("\nCreating batch...")
            await batch.create()
            print(f"Batch created with ID: {batch.id}")
            print(f"Status after creation: {batch.status}")
            print(f"Size in bytes: {batch.size_in_bytes}")

            print(f"\nStarting polling (checking every 5 seconds, max 60 seconds)...")
            final_status = await batch.start_polling(
                interval_seconds=5,
                timeout_seconds=60,
                callback=lambda status: print(f"  -> Batch status: {status}")
            )

            print(f"\nFinal batch status: {final_status}")

            wrapped_results = []
            if batch.messages:

                wrapped_results = wrap_batch_results(batch.messages, provider_name.lower())
                print(f"\n🎯 Clean Results:")
                for i, result in enumerate(wrapped_results, 1):
                    print(f"  Message {i} (ID: {result.message_id}):")
                    print(f"    ✅ Success: {result.success}")
                    print(f"    💬 Text: {result.text_content}")
                    print(f"    🤖 Model: {result.model}")
                    print(f"    🏢 Provider: {result.provider}")

                    if result.usage:
                        print(f"    🔢 Tokens: {result.usage.input_tokens} in + {result.usage.output_tokens} out = {result.usage.total_tokens} total")
                        if result.usage.cache_read_tokens > 0:
                            print(f"    💾 Cache: {result.usage.cache_read_tokens} tokens read from cache")

                    if result.has_error:
                        print(f"    ❌ Error: {result.error_message}")

                    # Show content blocks details if needed
                    if len(result.content_blocks) > 1:
                        print(f"    📄 Content blocks: {len(result.content_blocks)}")
                        for j, block in enumerate(result.content_blocks):
                            print(f"      Block {j+1}: {block.type.value} - {block.content[:50]}...")

                    print()
            else:
                print("No messages in the batch.")

            successful = sum(1 for r in wrapped_results if r.success)
            total_tokens = sum(r.usage.total_tokens for r in wrapped_results if r.usage)
            print(f"📊 Summary: {successful}/{len(wrapped_results)} successful, {total_tokens} total tokens")

        except Exception as e:
            print(f"Error testing {provider_name}: {e}")
            import traceback
            traceback.print_exc()

    # Test both providers
    print("Make sure you have OPENAI_API_KEY and ANTHROPIC_API_KEY environment variables set!")

    # if os.getenv("OPENAI_API_KEY"):
    #     await process(model_record_openai, "OpenAI")
    # else:
    #     print("Skipping OpenAI test - no OPENAI_API_KEY found")

    if os.getenv("ANTHROPIC_API_KEY"):
        await process(model_record_anthropic, "Anthropic")
    else:
        print("Skipping Anthropic test - no ANTHROPIC_API_KEY found")


async def try_get_batch():
    openai_batch_id = "batch_68823873e078819089245731a4c410ee" # OpenAI
    openai_batch_id = "batch_68829a71a2ec81908616f864adcea527" # OpenAI with cache

    anthropic_batch_id = "msgbatch_018FGFK9N4Y5GoBrmu4HsUjN" # Anthropic
    anthropic_batch_id = "msgbatch_01VTx75r9B6n1BrKfhvVHRqy" # Anthropic with cache
    anthropic_batch_id = "msgbatch_0193DmxE5h4FdV7iSWP3NQrL" # Anthropic with cache

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
    batch_id = openai_batch_id
    batch_id = anthropic_batch_id

    model_record = model_record_openai
    model_record = model_record_anthropic

    return await get_batch(batch_id=batch_id, model_record=model_record)

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv, find_dotenv

    load_dotenv(find_dotenv())

    import asyncio
    # asyncio.run(try_batch())

    asyncio.run(try_get_batch())
