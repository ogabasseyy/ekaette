"""DynamoDB adapter for voice call state records."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


class DynamoCallStore:
    """Persist call lifecycle events for SIP and WA sessions."""

    def __init__(self, table_name: str | None = None, *, region: str | None = None) -> None:
        self.table_name = table_name or os.getenv("DYNAMODB_CALLS_TABLE", "ekaette_wa_calls")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)

    async def set_active(
        self,
        *,
        call_id: str,
        tenant_id: str,
        company_id: str,
        started_at: float,
    ) -> None:
        await asyncio.to_thread(
            self._table.put_item,
            Item={
                "pk": f"call#{call_id}",
                "sk": "state",
                "tenant_id": tenant_id,
                "company_id": company_id,
                "status": "active",
                "started_at": Decimal(str(started_at)),
            },
        )

    async def set_terminated(
        self,
        *,
        call_id: str,
        ended_at: float,
        duration_seconds: float,
    ) -> None:
        def _update() -> None:
            try:
                self._table.update_item(
                    Key={"pk": f"call#{call_id}", "sk": "state"},
                    UpdateExpression="SET #st=:st, ended_at=:ended_at, duration_seconds=:duration_seconds",
                    ConditionExpression="attribute_exists(pk) AND attribute_exists(sk)",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":st": "terminated",
                        ":ended_at": Decimal(str(ended_at)),
                        ":duration_seconds": Decimal(str(duration_seconds)),
                    },
                )
            except ClientError as exc:
                error_code = str(exc.response.get("Error", {}).get("Code", ""))
                if error_code == "ConditionalCheckFailedException":
                    logger.info(
                        "Skipping call termination update for missing call state",
                        extra={"call_id": call_id},
                    )
                    return
                raise

        await asyncio.to_thread(_update)

    async def get(self, *, call_id: str) -> dict[str, Any] | None:
        response = await asyncio.to_thread(
            self._table.get_item,
            Key={"pk": f"call#{call_id}", "sk": "state"},
        )
        item = response.get("Item")
        return item if isinstance(item, dict) else None
