"""DynamoDB adapter for registry template/company documents."""

from __future__ import annotations

import os
from typing import Any

import boto3


class DynamoRegistryStore:
    """Registry lookups by tenant/company/template IDs."""

    def __init__(self, table_name: str | None = None, *, region: str | None = None) -> None:
        self.table_name = table_name or os.getenv("DYNAMODB_REGISTRY_TABLE", "ekaette_registry")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)

    async def get_company(self, *, tenant_id: str, company_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(
            Key={
                "pk": f"tenant#{tenant_id}",
                "sk": f"company#{company_id}",
            }
        )
        item = response.get("Item")
        return item if isinstance(item, dict) else None

    async def get_template(self, *, tenant_id: str, template_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(
            Key={
                "pk": f"tenant#{tenant_id}",
                "sk": f"template#{template_id}",
            }
        )
        item = response.get("Item")
        return item if isinstance(item, dict) else None

