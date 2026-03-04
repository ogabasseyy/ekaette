"""DynamoDB adapter for lightweight session state."""

from __future__ import annotations

import os
from typing import Any

import boto3


class DynamoSessionStore:
    """Session state persistence for Nova runtime."""

    def __init__(self, table_name: str | None = None, *, region: str | None = None) -> None:
        self.table_name = table_name or os.getenv("DYNAMODB_SESSIONS_TABLE", "ekaette_sessions")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)

    async def get(self, *, user_id: str, session_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(
            Key={
                "pk": f"user#{user_id}",
                "sk": f"session#{session_id}",
            }
        )
        item = response.get("Item")
        if not isinstance(item, dict):
            return None
        return item.get("state") if isinstance(item.get("state"), dict) else None

    async def upsert(self, *, user_id: str, session_id: str, state: dict[str, Any]) -> None:
        self._table.put_item(
            Item={
                "pk": f"user#{user_id}",
                "sk": f"session#{session_id}",
                "state": state,
            }
        )

