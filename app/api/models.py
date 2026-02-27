"""Shared API request models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdminCompanyUpsertPayload(BaseModel):
    """Admin API payload for creating/updating tenant company docs."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    company_id: str = Field(
        alias="companyId",
        min_length=2,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    display_name: str = Field(alias="displayName", min_length=2, max_length=120)
    industry_template_id: str = Field(
        alias="industryTemplateId",
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    status: str = Field(default="active", min_length=2, max_length=32)
    connectors: dict[str, object] = Field(default_factory=dict)
    overview: str | None = Field(default=None, max_length=2000)
    facts: dict[str, object] = Field(default_factory=dict)
    links: list[str] = Field(default_factory=list)
    tenant_id: str | None = Field(default=None, alias="tenantId")


class AdminCompanyUpdatePayload(BaseModel):
    """Admin API payload for updating an existing tenant company doc."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    display_name: str | None = Field(default=None, alias="displayName", min_length=2, max_length=120)
    industry_template_id: str | None = Field(
        default=None,
        alias="industryTemplateId",
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    status: str | None = Field(default=None, min_length=2, max_length=32)
    connectors: dict[str, object] | None = None
    overview: str | None = Field(default=None, max_length=2000)
    facts: dict[str, object] | None = None
    links: list[str] | None = None
    tenant_id: str | None = Field(default=None, alias="tenantId")


class AdminKnowledgeImportTextPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    knowledge_id: str | None = Field(default=None, alias="knowledgeId", max_length=120)
    title: str | None = Field(default=None, max_length=160)
    text: str = Field(min_length=1, max_length=20000)
    tags: list[str] = Field(default_factory=lambda: ["general"])
    source: str = Field(default="text", min_length=2, max_length=32)
    url: str | None = Field(default=None, max_length=2048)


class AdminKnowledgeImportUrlPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    knowledge_id: str | None = Field(default=None, alias="knowledgeId", max_length=120)
    url: str = Field(min_length=3, max_length=2048)
    title: str | None = Field(default=None, max_length=160)
    text: str | None = Field(default=None, max_length=20000)
    tags: list[str] = Field(default_factory=lambda: ["url"])
    source: str = Field(default="url", min_length=2, max_length=32)


class AdminConnectorPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    connector_id: str | None = Field(default=None, alias="connectorId", max_length=80)
    provider: str = Field(min_length=2, max_length=64)
    enabled: bool = True
    capabilities: list[str] = Field(default_factory=list)
    secret_ref: str | None = Field(default=None, alias="secretRef", max_length=512)
    config: dict[str, object] = Field(default_factory=dict)


class AdminProductsImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[dict[str, object]] = Field(min_length=1, max_length=500)
    data_tier: str = Field(default="admin", min_length=2, max_length=32)


class AdminBookingSlotsImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[dict[str, object]] = Field(min_length=1, max_length=500)
    data_tier: str = Field(default="admin", min_length=2, max_length=32)


class AdminCompanyExportPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    include_runtime_data: bool = Field(default=True, alias="includeRuntimeData")


class AdminRetentionPurgePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    older_than_days: int = Field(alias="olderThanDays", ge=1, le=3650)
    collections: list[str] = Field(default_factory=lambda: ["knowledge"])
    data_tier: str | None = Field(default=None, alias="dataTier", max_length=32)
