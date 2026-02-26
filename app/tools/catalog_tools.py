"""Catalog tools — product search and recommendations.

Uses Firestore for catalog storage. Falls back to basic query
when Vertex AI Search is unavailable. Queries are tenant/company-scoped
when session state contains canonical keys.
"""

import asyncio
import logging
from typing import Any

from app.tools.scoped_queries import scoped_collection_or_global

logger = logging.getLogger(__name__)

_firestore_db: Any = None


def _get_firestore_db() -> Any | None:
    """Get or create Firestore client. Returns None if unavailable."""
    global _firestore_db
    if _firestore_db is not None:
        return _firestore_db
    try:
        from google.cloud import firestore
        _firestore_db = firestore.Client()
        return _firestore_db
    except Exception as exc:
        logger.warning("Firestore client unavailable: %s", exc)
        return None


def _normalized_tokens(query: str) -> list[str]:
    return [token for token in query.lower().strip().split() if token]


def _product_matches_query(product: dict[str, Any], query: str) -> bool:
    tokens = _normalized_tokens(query)
    if not tokens:
        return True

    features = product.get("features", [])
    haystack_parts = [
        str(product.get("name", "")),
        str(product.get("brand", "")),
        str(product.get("category", "")),
        str(product.get("description", "")),
        " ".join(str(item) for item in features) if isinstance(features, list) else "",
    ]
    haystack = " ".join(haystack_parts).lower()
    return all(token in haystack for token in tokens)


async def search_catalog(
    query: str,
    category: str | None = None,
    max_results: int = 10,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Search the product catalog.

    Uses Firestore queries. A future upgrade could use Vertex AI Search
    for semantic matching.

    Args:
        query: Search query string.
        category: Optional category filter.
        max_results: Maximum number of results to return.
        tool_context: ADK ToolContext for tenant/company scoping.

    Returns:
        Dict with list of matching products.
    """
    db = _get_firestore_db()
    if db is None:
        return {"error": "Catalog service unavailable", "products": []}

    try:
        query_text = query.strip()
        collection = scoped_collection_or_global(db, tool_context, "products")
        if collection is None:
            return {"error": "Catalog service unavailable", "products": []}

        if category:
            collection = collection.where("category", "==", category)

        fetch_limit = min(max(max_results * 5, max_results), 100)
        collection = collection.limit(fetch_limit)

        docs = await asyncio.to_thread(lambda: list(collection.stream()))
        products: list[dict[str, Any]] = []
        for doc in docs:
            product = doc.to_dict()
            product["id"] = doc.id
            if not _product_matches_query(product, query_text):
                continue
            products.append(product)
            if len(products) >= max_results:
                break

        return {"query": query_text, "products": products}

    except Exception as exc:
        logger.error("Catalog search failed: %s", exc)
        return {"error": str(exc), "products": []}
