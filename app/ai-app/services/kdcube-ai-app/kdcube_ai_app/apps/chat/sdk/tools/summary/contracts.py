# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# sdk/tools/summary/contracts.py

from __future__ import annotations

from typing import List, Literal
from pydantic import BaseModel, Field, field_validator, ConfigDict

NONE = "NONE"


class ToolCallSummaryInput(BaseModel):
    """
    Input section of the tool-call summary.

    All fields are REQUIRED. The LLM must always emit them.
    If a field is unknown, it must explicitly use the sentinel
    value NONE / [NONE], not omit the field.
    """

    model_config = ConfigDict(extra="forbid")

    call_reason: str = Field(
        ...,
        description="≤12 words; what this step tried to achieve.",
    )
    key_params: List[str] = Field(
        ...,
        description="1–3 short items; params that affect quality/coverage.",
    )

    @field_validator("call_reason", mode="before")
    @classmethod
    def _call_reason_none(cls, v):
        # Field is required; this only runs if the field is present.
        if v is None:
            return NONE
        s = str(v).strip()
        return s if s else NONE

    @field_validator("key_params", mode="before")
    @classmethod
    def _key_params_none(cls, v):
        # Field is required; this only runs if the field is present.
        if v is None:
            return [NONE]
        if isinstance(v, str):
            s = v.strip()
            return [s] if s else [NONE]
        if isinstance(v, list):
            cleaned = [str(x).strip() for x in v if str(x).strip()]
            return cleaned if cleaned else [NONE]
        s = str(v).strip()
        return [s] if s else [NONE]


class ToolCallSummaryOutput(BaseModel):
    """
    Output section of the tool-call summary.

    All fields are REQUIRED. Missing fields should cause validation to fail.
    """

    model_config = ConfigDict(extra="forbid")

    completeness: Literal["success", "partial", "failed_empty"] = Field(
        ...,
        description="Degree of success vs goal.",
    )
    structural_summary: str = Field(
        ...,
        description="Compact structure only; no long quotes.",
    )
    semantic_summary: str = Field(
        ...,
        description="What actually came back; telegraphic.",
    )
    # Compact encoding: "aspect=<...>;status=<covered|partially_covered|missing>|aspect=...;status=..."
    coverage_by_aspect: str = Field(
        ...,
        description="Compact aspect/status string; NEVER omitted.",
    )

    @field_validator("structural_summary", "semantic_summary", "coverage_by_aspect", mode="before")
    @classmethod
    def _string_none(cls, v):
        # Required fields; this only runs if the field is present.
        if v is None:
            return NONE
        s = str(v).strip()
        return s if s else NONE


class ToolCallSummaryStrategy(BaseModel):
    """
    Strategy / meta section.

    All fields are REQUIRED. LLM must ALWAYS emit adequacy, risks, and main_next_move.
    """

    model_config = ConfigDict(extra="forbid")

    adequacy: Literal["full", "partial", "poor"] = Field(
        ...,
        description="Build-on safety signal.",
    )
    risks: List[str] = Field(
        ...,
        description="0–4 short risks; use [NONE] if truly none.",
    )
    main_next_move: str = Field(
        ...,
        description="At most ONE fix direction; NONE if none.",
    )

    @field_validator("risks", mode="before")
    @classmethod
    def _risks_none(cls, v):
        # Required field; this only runs if the field is present.
        if v is None:
            return [NONE]
        if isinstance(v, str):
            s = v.strip()
            return [s] if s else [NONE]
        if isinstance(v, list):
            cleaned = [str(x).strip() for x in v if str(x).strip()]
            return cleaned if cleaned else [NONE]
        s = str(v).strip()
        return [s] if s else [NONE]

    @field_validator("main_next_move", mode="before")
    @classmethod
    def _next_move_none(cls, v):
        # Required field; this only runs if the field is present.
        if v is None:
            return NONE
        s = str(v).strip()
        return s if s else NONE


class ToolCallSummaryJSON(BaseModel):
    """
    Minimal structured summary.

    All sections and all fields are REQUIRED.

    - If the LLM doesn't know something, it must output the sentinel
      'NONE' (or ['NONE']) explicitly.
    - If any field is omitted, validation MUST fail so we can fall back
      to raw_data.
    """

    model_config = ConfigDict(extra="forbid")

    input: ToolCallSummaryInput = Field(
        ...,
        description="Summary of the goal and key params.",
    )
    output: ToolCallSummaryOutput = Field(
        ...,
        description="Summary of the tool output.",
    )
    strategy: ToolCallSummaryStrategy = Field(
        ...,
        description="Meta-judgment and next move.",
    )

    def to_md(self) -> str:
        """
        Convert ToolCallSummaryJSON to markdown format for display.

        Returns a human-readable markdown summary of the tool call.
        """
        return f"""
## Role & Inputs
- **Goal**: {self.input.call_reason}
- **Key Params**: {', '.join(self.input.key_params)}

## Output
- **Completeness**: {self.output.completeness}
- **Structural Summary**: {self.output.structural_summary}
- **Semantic Summary**: {self.output.semantic_summary}
- **Coverage by Aspect**: {self.output.coverage_by_aspect}

## Risks, Quality & Next Moves
- **Adequacy**: {self.strategy.adequacy}
- **Risks**: {', '.join(self.strategy.risks)}
- **Next Move**: {self.strategy.main_next_move}
        """
