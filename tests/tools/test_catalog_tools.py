"""Tests for catalog tools — TDD for S10."""

from unittest.mock import MagicMock, patch

import pytest


SAMPLE_PRODUCTS = [
    {
        "id": "prod-001",
        "name": "iPhone 15 Pro",
        "price": 850_000,
        "currency": "NGN",
        "category": "smartphones",
        "brand": "Apple",
        "in_stock": True,
        "features": ["A17 Pro chip", "48MP camera", "Titanium design"],
    },
    {
        "id": "prod-002",
        "name": "Samsung Galaxy S24",
        "price": 620_000,
        "currency": "NGN",
        "category": "smartphones",
        "brand": "Samsung",
        "in_stock": True,
        "features": ["Snapdragon 8 Gen 3", "AI features", "120Hz display"],
    },
    {
        "id": "prod-003",
        "name": "Google Pixel 8",
        "price": 450_000,
        "currency": "NGN",
        "category": "smartphones",
        "brand": "Google",
        "in_stock": False,
        "features": ["Tensor G3", "Best camera AI", "7 years updates"],
    },
]


class TestSearchCatalog:
    """Test catalog search functionality."""

    @pytest.mark.asyncio
    async def test_returns_matching_products(self):
        """Should return products matching search query."""
        from app.tools.catalog_tools import search_catalog

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_docs = []
        for prod in SAMPLE_PRODUCTS:
            doc = MagicMock()
            doc.id = prod["id"]
            doc.to_dict.return_value = prod
            mock_docs.append(doc)

        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter(mock_docs)
        mock_db.collection.return_value = mock_query

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db):
            result = await search_catalog(query="iPhone")

        assert "products" in result
        assert len(result["products"]) >= 1

    @pytest.mark.asyncio
    async def test_query_filters_results_by_keyword(self):
        """Keyword query should exclude non-matching products."""
        from app.tools.catalog_tools import search_catalog

        mock_db = MagicMock()
        mock_query = MagicMock()
        docs = []
        for prod in SAMPLE_PRODUCTS:
            doc = MagicMock()
            doc.id = prod["id"]
            doc.to_dict.return_value = prod
            docs.append(doc)

        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter(docs)
        mock_db.collection.return_value = mock_query

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db):
            result = await search_catalog(query="iPhone")

        names = [item["name"] for item in result["products"]]
        assert names
        assert all("iphone" in name.lower() for name in names)

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_matches(self):
        """Should return empty list when no products match."""
        from app.tools.catalog_tools import search_catalog

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter([])
        mock_db.collection.return_value = mock_query

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db):
            result = await search_catalog(query="nonexistent-product-xyz")

        assert result["products"] == []

    @pytest.mark.asyncio
    async def test_filters_by_category(self):
        """Should filter by category when provided."""
        from app.tools.catalog_tools import search_catalog

        mock_db = MagicMock()
        mock_query = MagicMock()
        phone_docs = []
        for prod in SAMPLE_PRODUCTS:
            if prod["category"] == "smartphones":
                doc = MagicMock()
                doc.id = prod["id"]
                doc.to_dict.return_value = prod
                phone_docs.append(doc)

        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter(phone_docs)
        mock_db.collection.return_value = mock_query

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db):
            result = await search_catalog(
                query="phone", category="smartphones"
            )

        assert "products" in result

    @pytest.mark.asyncio
    async def test_returns_error_when_db_unavailable(self):
        """Should return error when Firestore is unavailable."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="iPhone")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_product_includes_required_fields(self):
        """Each product should include name, price, and availability."""
        from app.tools.catalog_tools import search_catalog

        mock_db = MagicMock()
        mock_query = MagicMock()
        doc = MagicMock()
        doc.id = SAMPLE_PRODUCTS[0]["id"]
        doc.to_dict.return_value = SAMPLE_PRODUCTS[0]

        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter([doc])
        mock_db.collection.return_value = mock_query

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db):
            result = await search_catalog(query="iPhone 15")

        if result["products"]:
            product = result["products"][0]
            assert "name" in product
            assert "price" in product
            assert "in_stock" in product
