"""Catalog tools — product search and recommendations.

Uses Firestore for catalog storage. Falls back to basic query
when Vertex AI Search is unavailable. Queries are tenant/company-scoped
when session state contains canonical keys.
"""

import asyncio
import logging
import re
from typing import Any

from app.tools.scoped_queries import scoped_collection

logger = logging.getLogger(__name__)

_firestore_db: Any = None

_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "available",
    "buy",
    "can",
    "do",
    "for",
    "get",
    "have",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "need",
    "of",
    "one",
    "please",
    "show",
    "the",
    "to",
    "want",
    "which",
    "with",
    "you",
}

_CATEGORY_ALIASES: dict[str, set[str]] = {
    "security": {
        "security",
        "cctv",
        "ctv",
        "camera",
        "cameras",
        "surveillance",
        "securitycamera",
        "securitycameras",
    },
    "smartphones": {"smartphone", "smartphones", "phone", "phones", "mobile", "mobiles"},
    "tablets": {"tablet", "tablets", "ipad", "tab", "tabs"},
    "laptops": {"laptop", "laptops", "notebook", "notebooks", "macbook"},
    "accessories": {"accessory", "accessories", "charger", "chargers", "powerbank"},
}

_DEMO_FALLBACK_PRODUCTS: list[dict[str, Any]] = [
    {
        "id": "prod-cctv-bundle-4ch",
        "name": "CCTV Security Bundle (4-Camera + DVR)",
        "price": 980_000,
        "currency": "NGN",
        "category": "security",
        "brand": "Hikvision",
        "in_stock": True,
        "features": [
            "4 x 2MP HD cameras",
            "8-channel DVR",
            "1TB surveillance HDD",
            "60m cabling kit",
            "12V power supply",
        ],
        "description": (
            "Complete starter CCTV kit for shops, warehouses, and homes "
            "with same-week installation support."
        ),
        "data_tier": "demo",
    },
    {
        "id": "prod-anker-powerbank-26k",
        "name": "Anker PowerCore 26800",
        "price": 32_000,
        "currency": "NGN",
        "category": "accessories",
        "brand": "Anker",
        "in_stock": True,
        "features": ["26800mAh", "Dual USB-A", "PowerIQ fast charging"],
        "description": "High-capacity portable charger for multiple device charges.",
        "data_tier": "demo",
    },
]


_QUERY_ALIAS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Common ASR drift for "CCTV" in calls.
    (re.compile(r"\bct\s*scan\b", flags=re.IGNORECASE), "cctv"),
    (re.compile(r"\bctv\b", flags=re.IGNORECASE), "cctv"),
    (re.compile(r"\bcc\s*tv\b", flags=re.IGNORECASE), "cctv"),
    (re.compile(r"\bc\s*t\s*v\b", flags=re.IGNORECASE), "cctv"),
    (re.compile(r"\bsecurity\s+cam(?:era)?s?\b", flags=re.IGNORECASE), "cctv"),
)

_STORAGE_TOKEN_PATTERN = re.compile(r"^(?:\d+gb|\d+tb)$", flags=re.IGNORECASE)


def _currency_name(currency: object) -> str:
    raw = str(currency or "").strip().upper()
    if raw == "NGN":
        return "naira"
    return raw or "currency"


def _format_price_display(price: object, currency: object) -> str:
    try:
        amount = int(price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(price)
    currency_name = _currency_name(currency)
    if currency_name == "naira":
        return f"{amount:,} naira"
    return f"{currency_name} {amount:,}"


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
    return [token for token in re.findall(r"[a-z0-9]+", query.lower()) if token]


def _normalize_query_text(query: str) -> str:
    normalized = query or ""
    for pattern, replacement in _QUERY_ALIAS_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def _significant_query_tokens(query: str) -> list[str]:
    normalized_query = _normalize_query_text(query)
    return [
        token
        for token in _normalized_tokens(normalized_query)
        if (len(token) >= 3 or token.isdigit()) and token not in _QUERY_STOPWORDS
    ]


def _extract_storage_tokens(query: str) -> set[str]:
    normalized_query = _normalize_query_text(query)
    return {
        token
        for token in _normalized_tokens(normalized_query)
        if _STORAGE_TOKEN_PATTERN.fullmatch(token)
    }


def _product_storage_tokens(product: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    variants = product.get("storage_variants")
    if isinstance(variants, list):
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            storage = variant.get("storage")
            if not isinstance(storage, str):
                continue
            normalized = "".join(storage.lower().split())
            if _STORAGE_TOKEN_PATTERN.fullmatch(normalized):
                tokens.add(normalized)
    features = product.get("features")
    if isinstance(features, list):
        for item in features:
            if not isinstance(item, str):
                continue
            for token in _normalized_tokens(item):
                if _STORAGE_TOKEN_PATTERN.fullmatch(token):
                    tokens.add(token)
    return tokens


def _matched_storage_variant(product: dict[str, Any], query: str) -> dict[str, Any] | None:
    requested_storage = _extract_storage_tokens(query)
    if not requested_storage:
        return None
    variants = product.get("storage_variants")
    if not isinstance(variants, list):
        return None
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        storage = variant.get("storage")
        if not isinstance(storage, str):
            continue
        normalized = "".join(storage.lower().split())
        if normalized in requested_storage:
            return variant
    return None


def _product_matches_query(product: dict[str, Any], query: str) -> bool:
    tokens = _significant_query_tokens(query)
    if not tokens:
        return True

    storage_tokens = _extract_storage_tokens(query)
    if storage_tokens:
        if not storage_tokens.issubset(_product_storage_tokens(product)):
            return False

    features = product.get("features", [])
    haystack_parts = [
        str(product.get("name", "")),
        str(product.get("brand", "")),
        str(product.get("category", "")),
        str(product.get("description", "")),
        " ".join(str(item) for item in features) if isinstance(features, list) else "",
        " ".join(sorted(_product_storage_tokens(product))),
    ]
    haystack = " ".join(haystack_parts).lower()
    haystack_tokens = set(_normalized_tokens(haystack))

    numeric_tokens = {token for token in tokens if token.isdigit()}
    if numeric_tokens and not numeric_tokens.issubset(haystack_tokens):
        return False

    matches = 0
    non_storage_tokens = [token for token in tokens if token not in storage_tokens]
    for token in non_storage_tokens:
        if token in haystack_tokens or token in haystack:
            matches += 1

    token_count = len(non_storage_tokens)
    if token_count == 0:
        return True
    if storage_tokens or numeric_tokens:
        return matches == token_count
    if token_count == 1:
        return matches == 1
    if token_count == 2:
        return matches >= 1
    return matches >= max(2, (token_count + 1) // 2)


def _canonical_category(value: str) -> str:
    tokens = _normalized_tokens(value)
    if not tokens:
        return ""
    for canonical, aliases in _CATEGORY_ALIASES.items():
        if canonical in tokens:
            return canonical
        if any(token in aliases for token in tokens):
            return canonical
    return tokens[0]


def _product_matches_category(product: dict[str, Any], category: str | None) -> bool:
    if not isinstance(category, str) or not category.strip():
        return True

    requested = category.strip().lower()
    product_category = str(product.get("category", "")).strip().lower()
    if not product_category:
        return False

    canonical_requested = _canonical_category(requested)
    canonical_product = _canonical_category(product_category)
    if canonical_requested and canonical_product and canonical_requested == canonical_product:
        return True

    requested_tokens = set(_normalized_tokens(requested))
    product_tokens = set(_normalized_tokens(product_category))
    if requested_tokens and requested_tokens.issubset(product_tokens):
        return True
    return bool(requested_tokens & product_tokens)


def _fallback_products(query: str, category: str | None, max_results: int) -> list[dict[str, Any]]:
    safe_max = _safe_max_results(max_results, default=10)
    products: list[dict[str, Any]] = []
    for item in _DEMO_FALLBACK_PRODUCTS:
        if not _product_matches_category(item, category):
            continue
        if not _product_matches_query(item, query):
            continue
        products.append(_format_product(dict(item), query=query))
        if len(products) >= safe_max:
            break
    return products


def _format_product(product: dict[str, Any], *, query: str = "") -> dict[str, Any]:
    """Format a product for display, flattening storage variants into the price field."""
    variants = product.get("storage_variants")
    formatted = dict(product)
    formatted["currency_name"] = _currency_name(formatted.get("currency"))
    if isinstance(formatted.get("price"), (int, float)):
        formatted["price_display"] = _format_price_display(
            formatted.get("price"),
            formatted.get("currency"),
        )
    if not variants or not isinstance(variants, list):
        return formatted
    matched_variant = _matched_storage_variant(product, query)
    if matched_variant is not None:
        formatted["price"] = matched_variant.get("price", formatted.get("price"))
        formatted["storage"] = matched_variant.get("storage", "")
        formatted["price_display"] = _format_price_display(
            formatted.get("price"),
            formatted.get("currency"),
        )
        formatted.pop("storage_variants", None)
        return formatted
    # Replace flat price with variant breakdown
    default_currency = formatted.get("currency", "NGN")
    prices = [
        f"{v['storage']}: {_format_price_display(v['price'], v.get('currency', default_currency))}"
        for v in variants
        if isinstance(v, dict) and "storage" in v and "price" in v
    ]
    if prices:
        formatted["price"] = " | ".join(prices)
        formatted["price_display"] = formatted["price"]
        # Remove raw numeric price and variants to avoid model picking the flat number
        formatted.pop("storage_variants", None)
    return formatted


def _safe_max_results(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 50))


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
    safe_max = _safe_max_results(max_results, default=10)
    query_text = (query or "").strip()
    db = _get_firestore_db()
    if db is None:
        return {
            "error": "Catalog service unavailable; using demo fallback data.",
            "source": "demo_fallback",
            "query": query_text,
            "products": _fallback_products(query_text, category, safe_max),
        }

    try:
        collection = scoped_collection(db, tool_context, "products")
        if collection is None:
            return {
                "error": "Catalog scope unavailable; using demo fallback data.",
                "source": "demo_fallback",
                "query": query_text,
                "products": _fallback_products(query_text, category, safe_max),
            }

        fetch_limit = min(max(safe_max * 5, safe_max), 100)
        collection = collection.limit(fetch_limit)

        docs = await asyncio.to_thread(lambda: list(collection.stream()))
        products: list[dict[str, Any]] = []
        for doc in docs:
            product = doc.to_dict()
            product["id"] = doc.id
            if not _product_matches_category(product, category):
                continue
            if not _product_matches_query(product, query_text):
                continue
            products.append(_format_product(product, query=query_text))
            if len(products) >= safe_max:
                break

        if not products:
            fallback = _fallback_products(query_text, category, safe_max)
            if fallback:
                return {
                    "query": query_text,
                    "source": "demo_fallback",
                    "products": fallback,
                }

        return {"query": query_text, "products": products}

    except Exception:
        logger.exception("Catalog search failed")
        return {
            "error": "Catalog lookup failed; using demo fallback data.",
            "source": "demo_fallback",
            "query": query_text,
            "products": _fallback_products(query_text, category, safe_max),
        }
