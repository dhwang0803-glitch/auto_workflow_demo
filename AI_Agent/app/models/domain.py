"""Domain classifier wire shapes (PLAN_12 W2-2)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Must stay aligned with AI_Agent/data/policies/*.yaml filenames.
# "other" is the safety-net category for free-text inputs that do not fit;
# the wizard treats it as "no seed policies — start blank".
DomainCategory = Literal[
    "ecommerce",
    "services",
    "consulting",
    "content",
    "nonprofit",
    "other",
]


class DomainClassifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DomainClassification(BaseModel):
    domain: DomainCategory
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
