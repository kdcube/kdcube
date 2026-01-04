from typing import Optional, Dict, Any, List, AsyncIterator
import aiohttp, json
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage, AIMessage, AIMessageChunk
from kdcube_ai_app.infra.service_hub.message_utils import (
    extract_message_blocks,
    blocks_to_text,
    blocks_to_gemini_parts,
)

def _anthropic_blocks_to_gemini_parts(blocks, default_cache_ctrl=None) -> list[dict]:
    """
    Convert Anthropic-style blocks (our message_blocks) into Gemini 'parts'.

    Normalization rules:

    - Raw strings → {"text": "..."}
    - Any dict with a string `text` → {"text": text}
    - Any dict with a string `content` → {"text": content}
    - Any dict with list `content` → recursively flatten that list
      (e.g. tool_result/tool_use containers)
    - Anything else → stringified into a text part

    cache_control is ignored (Gemini has no per-part cache hints).
    """
    return blocks_to_gemini_parts(blocks or [])

class GeminiModelClient:
    """
    Minimal, clean client for Gemini API (text only for now).

    - Uses official REST endpoints: /v1beta/models/...:generateContent / :streamGenerateContent
    - Converts LangChain messages → Gemini "contents"/"systemInstruction"
    - Optional context caching via cachedContents (single cache per client instance)
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
            self,
            api_key: str,
            model_name: str,
            temperature: float = 0.7,
            cache_enabled: bool = False,
            cache_ttl_seconds: int = 3600,
    ):
        self.api_key = api_key
        self.model = model_name
        self.model_name = model_name  # e.g. "models/gemini-2.5-flash-001"
        self.temperature = float(temperature)
        self.cache_enabled = cache_enabled
        self.cache_ttl_seconds = int(cache_ttl_seconds)

        from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
        self.logger = AgentLogger("GeminiModelClient")

        # cachedContents/<id> resource name
        self._cached_content_name: Optional[str] = None

    # ---------- internal helpers ----------

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

    def _model_path(self) -> str:
        # Allow callers to pass bare name ("gemini-2.5-flash-001") or full "models/..."
        if self.model_name.startswith("models/"):
            return self.model_name
        return f"models/{self.model_name}"

    @staticmethod
    def _blocks_to_text(blocks: List[Any]) -> str:
        """
        Collapse Anthropic-style content blocks into plain text.
        Uses the same normalization as _anthropic_blocks_to_gemini_parts.
        """
        return blocks_to_text(blocks)

    @staticmethod
    def _convert_langchain_to_gemini(
            messages: List[BaseMessage],
    ) -> tuple[Optional[dict], list[dict]]:
        """
        Convert LC messages into Gemini's JSON shape.

        Returns: (system_instruction, contents)
          - system_instruction: {"parts": [...]} or None
          - contents: list of {role, parts}
        """
        system_instruction: Optional[dict] = None
        system_text_parts: list[str] = []
        contents: list[dict] = []

        for msg in messages:
            blocks = extract_message_blocks(msg)

            # ---------- SystemMessage → systemInstruction only ----------
            if isinstance(msg, SystemMessage):
                if blocks:
                    parts = _anthropic_blocks_to_gemini_parts(blocks)
                    if system_instruction is None:
                        # IMPORTANT: no "role" field here
                        system_instruction = {"parts": parts}
                    else:
                        system_instruction["parts"].extend(parts)
                else:
                    text = getattr(msg, "content", "") or ""
                    if text:
                        system_text_parts.append(str(text))
                # System messages do NOT go into "contents"
                continue

            # ---------- Non-system messages → contents ----------
            if isinstance(msg, HumanMessage):
                role = "user"
            elif isinstance(msg, AIMessage):
                role = "model"
            else:
                role = "user"

            if blocks:
                parts = _anthropic_blocks_to_gemini_parts(blocks)
            else:
                text = getattr(msg, "content", "") or ""
                parts = [{"text": str(text)}]

            contents.append({"role": role, "parts": parts})

        # Plain-text system messages (no blocks)
        if system_instruction is None and system_text_parts:
            system_instruction = {
                # IMPORTANT: still no "role" here
                "parts": [{"text": "\n\n".join(system_text_parts)}],
            }

        return system_instruction, contents

    async def _ensure_context_cache(
            self,
            system_instruction: Optional[dict],
    ) -> None:
        """
        Create a cachedContents entry once per client instance, based on system_instruction.
        """
        if not self.cache_enabled:
            return
        if self._cached_content_name is not None:
            return
        if not system_instruction:
            # nothing static to cache
            return

        # For cachedContents, it's OK (and often clearer) to have a role.
        # Do NOT mutate system_instruction itself.
        cache_content = dict(system_instruction)
        cache_content.setdefault("role", "user")

        payload = {
            "model": self._model_path(),
            "contents": [cache_content],
            "ttl": f"{self.cache_ttl_seconds}s",
        }
        # url = f"{self.BASE_URL}/cachedContents?key={self.api_key}"
        url = f"{self.BASE_URL}/cachedContents"

        self.logger.start_operation(
            "gemini_create_cache",
            model=self.model_name,
            ttl_seconds=self.cache_ttl_seconds,
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        url,
                        headers=self._headers(),
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise Exception(f"Gemini cache HTTP {resp.status}: {body}")

                    data = await resp.json()
                    self._cached_content_name = data.get("name")
                    self.logger.finish_operation(
                        True,
                        f"created cache {self._cached_content_name}",
                    )
        except Exception as e:
            self.logger.log_error(e, "gemini_create_cache_failed")
            self.logger.finish_operation(False, "gemini_create_cache_failed")
            self._cached_content_name = None

    # ---------- non-streaming ----------

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> AIMessage:
        """
        Non-streaming call via :generateContent.
        """
        system_instruction, contents = self._convert_langchain_to_gemini(messages)

        # Optionally enable cache (one cache per client)
        await self._ensure_context_cache(system_instruction)

        gen_cfg = {
            "temperature": kwargs.get("temperature", self.temperature),
            "maxOutputTokens": kwargs.get("max_output_tokens", 1024),
        }

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_cfg,
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if self._cached_content_name:
            payload["cachedContent"] = self._cached_content_name

        # url = f"{self.BASE_URL}/{self._model_path()}:generateContent?key={self.api_key}"
        url = f"{self.BASE_URL}/{self._model_path()}:generateContent"

        self.logger.start_operation(
            "gemini_generate_content",
            model=self.model_name,
            msg_count=len(messages),
            has_cache=bool(self._cached_content_name),
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        url,
                        headers=self._headers(),
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"Gemini HTTP {resp.status}: {await resp.text()}")
                    data = await resp.json()

            # Extract text
            text_parts: list[str] = []
            for cand in data.get("candidates", []) or []:
                content = cand.get("content") or {}
                for part in content.get("parts", []) or []:
                    if "text" in part:
                        text_parts.append(part["text"])
            text = "".join(text_parts) if text_parts else "No response generated"

            # Extract basic usage if present
            usage = {}
            usage_meta = data.get("usageMetadata") or {}
            # normalize Gemini’s field names loosely
            if usage_meta:
                in_t = usage_meta.get("promptTokenCount") or usage_meta.get("inputTokens")

                # user-visible output tokens
                visible_out_t = usage_meta.get("candidatesTokenCount") or usage_meta.get("outputTokens")

                # internal thinking / reasoning tokens
                thinking_t = usage_meta.get("thoughtsTokenCount") or usage_meta.get("reasoningTokens")

                # billed output = visible + thinking
                billed_out_t = (visible_out_t or 0) + (thinking_t or 0)

                total_t = usage_meta.get("totalTokenCount") or (
                        (in_t or 0) + billed_out_t
                )

                usage = {
                    # legacy-style fields
                    "prompt_tokens": in_t or 0,
                    "completion_tokens": billed_out_t,

                    # **new** fields for accounting
                    "input_tokens": in_t or 0,
                    "output_tokens": billed_out_t,      # already includes thinking
                    "thinking_tokens": thinking_t or 0,
                    "visible_output_tokens": visible_out_t or 0,

                    "total_tokens": total_t or 0,
                }

            self.logger.log_model_call(self.model_name, sum(len(str(m.content)) for m in messages), len(text), True)
            self.logger.finish_operation(True, f"Generated {len(text)} characters")

            return AIMessage(
                content=text,
                additional_kwargs={
                    "usage": usage,
                    "provider_message_id": data.get("responseId"),
                    "model_name": self.model_name,
                },
            )

        except Exception as e:
            self.logger.log_error(e, "gemini_invoke_failed")
            self.logger.finish_operation(False, "gemini_invoke_failed")
            raise

    # ---------- streaming ----------

    async def astream(
            self,
            messages: List[BaseMessage],
            **kwargs,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        True streaming via :streamGenerateContent with SSE.

        Yields:
          - {"delta": "..."} for each visible text chunk
          - {"thought_delta": "..."} for each thought-summary chunk
          - then one final event:
            {"event": "final", "usage": {...}, "model_name": ..., "full_text": ..., "full_thoughts": ...}
        """
        system_instruction, contents = self._convert_langchain_to_gemini(messages)
        await self._ensure_context_cache(system_instruction)

        # ---- generation + thinking config ----
        gen_cfg: Dict[str, Any] = {
            "temperature": kwargs.get("temperature", self.temperature),
            "maxOutputTokens": kwargs.get("max_output_tokens", 1024),
            "responseModalities": ["TEXT"],
            # "thinkingLevel": "low" # or "high". Starting from Gemini 3 Pro.
        }

        thinking_budget = kwargs.get("thinking_budget") or 128
        include_thoughts = kwargs.get("include_thoughts", False)

        if include_thoughts or thinking_budget is not None:
            thinking_cfg: Dict[str, Any] = {}
            if include_thoughts:
                thinking_cfg["includeThoughts"] = True
            if thinking_budget is not None:
                thinking_cfg["thinkingBudget"] = int(thinking_budget)
            gen_cfg["thinkingConfig"] = thinking_cfg

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_cfg,
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if self._cached_content_name:
            payload["cachedContent"] = self._cached_content_name

        url = (
            f"{self.BASE_URL}/{self._model_path()}"
            f":streamGenerateContent?alt=sse"
        )

        self.logger.start_operation(
            "gemini_stream_generate_content",
            model=self.model_name,
            msg_count=len(messages),
            has_cache=bool(self._cached_content_name),
        )

        usage: Dict[str, int] = {}
        buf = []
        full_text = ""      # accumulated visible answer
        full_thoughts = ""  # accumulated thought summaries

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        url,
                        headers=self._headers(),
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=600),
                ) as resp:
                    if resp.status != 200:
                        raise Exception(
                            f"Gemini stream HTTP {resp.status}: {await resp.text()}"
                        )

                    async for raw in resp.content:
                        chunk = raw.decode("utf-8", errors="ignore")
                        if not chunk:
                            continue

                        for line in chunk.splitlines():
                            line = line.strip()
                            if not line or not line.startswith("data:"):
                                continue
                            buf.append(line)
                            data_str = line[len("data:"):].strip()
                            if data_str == "[DONE]":
                                # Not usually sent by Gemini, but harmless guard.
                                continue

                            try:
                                evt = json.loads(data_str)
                            except Exception:
                                continue

                            # ---- usage (often present only on last evt) ----
                            usage_meta = evt.get("usageMetadata") or {}
                            if usage_meta:
                                in_t = usage_meta.get("promptTokenCount") or usage_meta.get("inputTokens")
                                visible_out_t = usage_meta.get("candidatesTokenCount") or usage_meta.get("outputTokens")
                                thinking_t = usage_meta.get("thoughtsTokenCount") or usage_meta.get("reasoningTokens")
                                billed_out_t = (visible_out_t or 0) + (thinking_t or 0)
                                total_t = usage_meta.get("totalTokenCount") or ((in_t or 0) + billed_out_t)

                                usage = {
                                    "prompt_tokens": in_t or 0,
                                    "completion_tokens": billed_out_t,
                                    "input_tokens": in_t or 0,
                                    "output_tokens": billed_out_t,
                                    "thinking_tokens": thinking_t or 0,
                                    "visible_output_tokens": visible_out_t or 0,
                                    "total_tokens": total_t or 0,
                                }

                            # ---- text + thoughts ----
                            for cand in evt.get("candidates", []) or []:
                                content = cand.get("content") or {}
                                for part in content.get("parts", []) or []:
                                    text = part.get("text") or ""
                                    if not text:
                                        continue

                                    is_thought = bool(part.get("thought"))

                                    if is_thought:
                                        # Some models stream cumulative thoughts; diff against full_thoughts.
                                        if text.startswith(full_thoughts):
                                            thought_delta = text[len(full_thoughts):]
                                        else:
                                            thought_delta = text

                                        full_thoughts += thought_delta
                                        if thought_delta:
                                            yield {
                                                "event": "thinking.delta",
                                                "text": thought_delta,
                                            }
                                    else:
                                        if text.startswith(full_text):
                                            delta = text[len(full_text):]
                                        else:
                                            delta = text

                                        full_text += delta
                                        if delta:
                                            yield {
                                                "event": "text.delta",
                                                "text": delta,
                                            }

            # final event, for upstream accounting
            yield {
                "event": "final",
                "usage": usage,
                "model_name": self.model_name,
                "full_text": full_text,
                "full_thoughts": full_thoughts,
            }
            self.logger.finish_operation(True, "gemini_stream_complete")

        except Exception as e:
            self.logger.log_error(e, "gemini_stream_failed")
            self.logger.finish_operation(False, "gemini_stream_failed")
            # keep contract: always emit final event
            yield {
                "event": "final",
                "usage": usage,
                "model_name": self.model_name,
                "full_text": full_text,
                "full_thoughts": full_thoughts,
            }
            raise
