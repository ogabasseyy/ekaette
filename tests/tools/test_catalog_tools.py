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

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
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

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
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

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
            result = await search_catalog(query="nonexistent-product-xyz")

        assert result["products"] == []

    @pytest.mark.asyncio
    async def test_query_with_storage_variant_returns_matching_variant_only(self):
        """Storage-specific queries should require that variant and return its price."""
        from app.tools.catalog_tools import search_catalog

        products = [
            {
                "id": "prod-iphone-15-pro-max",
                "name": "iPhone 15 Pro Max",
                "price": 950_000,
                "currency": "NGN",
                "category": "smartphones",
                "brand": "Apple",
                "in_stock": True,
                "storage_variants": [
                    {"storage": "256GB", "price": 950_000},
                    {"storage": "512GB", "price": 1_100_000},
                ],
            },
            {
                "id": "prod-iphone-15-pro",
                "name": "iPhone 15 Pro",
                "price": 850_000,
                "currency": "NGN",
                "category": "smartphones",
                "brand": "Apple",
                "in_stock": True,
                "storage_variants": [
                    {"storage": "128GB", "price": 850_000},
                    {"storage": "256GB", "price": 950_000},
                ],
            },
        ]
        mock_query = MagicMock()
        docs = []
        for prod in products:
            doc = MagicMock()
            doc.id = prod["id"]
            doc.to_dict.return_value = prod
            docs.append(doc)

        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter(docs)

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=MagicMock()),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
            result = await search_catalog(query="iPhone 15 Pro 128GB")

        assert [item["name"] for item in result["products"]] == ["iPhone 15 Pro"]
        assert result["products"][0]["storage"] == "128GB"
        assert result["products"][0]["price"] == 850_000

    @pytest.mark.asyncio
    async def test_query_with_model_number_filters_other_iphone_models(self):
        """Numeric model tokens like 14/15 should constrain otherwise similar matches."""
        from app.tools.catalog_tools import search_catalog

        products = [
            {
                "id": "prod-iphone-15-pro",
                "name": "iPhone 15 Pro",
                "price": 850_000,
                "currency": "NGN",
                "category": "smartphones",
                "brand": "Apple",
                "in_stock": True,
                "storage_variants": [{"storage": "128GB", "price": 850_000}],
            },
            {
                "id": "prod-iphone-14",
                "name": "iPhone 14",
                "price": 520_000,
                "currency": "NGN",
                "category": "smartphones",
                "brand": "Apple",
                "in_stock": True,
                "storage_variants": [{"storage": "128GB", "price": 520_000}],
            },
        ]
        mock_query = MagicMock()
        docs = []
        for prod in products:
            doc = MagicMock()
            doc.id = prod["id"]
            doc.to_dict.return_value = prod
            docs.append(doc)

        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter(docs)

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=MagicMock()),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
            result = await search_catalog(query="iPhone 14 128GB")

        assert [item["name"] for item in result["products"]] == ["iPhone 14"]
        assert result["products"][0]["storage"] == "128GB"

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

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
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
    async def test_db_unavailable_uses_demo_fallback_for_cctv(self):
        """Firestore outage should still return CCTV demo fallback result."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="cctv", category="security")

        assert result.get("source") == "demo_fallback"
        products = result.get("products", [])
        assert isinstance(products, list)
        assert products
        assert "cctv" in str(products[0].get("name", "")).lower()

    @pytest.mark.asyncio
    async def test_db_unavailable_category_phrase_matches_security_catalog(self):
        """Category phrase like 'security cameras' should map to security products."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="cctv", category="security cameras")

        products = result.get("products", [])
        assert products
        assert any("cctv" in str(item.get("name", "")).lower() for item in products)

    @pytest.mark.asyncio
    async def test_natural_language_query_matches_cctv(self):
        """Natural-language query with filler words should still match CCTV product."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="to buy CCTV.", category=None)

        products = result.get("products", [])
        assert products
        assert "cctv" in str(products[0].get("name", "")).lower()

    @pytest.mark.asyncio
    async def test_asr_variant_ctv_query_matches_cctv(self):
        """ASR shorthand like CTV should normalize to CCTV search intent."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="by CTV do you have any available", category=None)

        products = result.get("products", [])
        assert products
        assert any("cctv" in str(item.get("name", "")).lower() for item in products)

    @pytest.mark.asyncio
    async def test_asr_variant_ct_scan_query_matches_cctv(self):
        """ASR drift 'CT scan' should still surface CCTV products in hardware context."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="do you have CT scan available", category=None)

        products = result.get("products", [])
        assert products
        assert any("cctv" in str(item.get("name", "")).lower() for item in products)

    @pytest.mark.asyncio
    async def test_query_with_no_product_terms_returns_options(self):
        """Intent-only follow-up queries should still return available options."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="i want to buy", category=None)

        products = result.get("products", [])
        assert products
        assert len(products) >= 1

    @pytest.mark.asyncio
    async def test_ambiguous_availability_question_returns_options(self):
        """Follow-up availability phrasing should return product choices."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(query="Which one do you have available?", category=None)

        products = result.get("products", [])
        assert products
        names = [str(item.get("name", "")) for item in products]
        assert any(name for name in names)

    @pytest.mark.asyncio
    async def test_firestore_path_category_phrase_matches_cctv(self):
        """Firestore path should also map category phrases like 'security cameras'."""
        from app.tools.catalog_tools import search_catalog

        mock_db = MagicMock()
        mock_query = MagicMock()
        cctv_doc = MagicMock()
        cctv_doc.id = "prod-cctv-bundle-4ch"
        cctv_doc.to_dict.return_value = {
            "name": "CCTV Security Bundle (4-Camera + DVR)",
            "price": 980000,
            "currency": "NGN",
            "category": "security",
            "brand": "Hikvision",
            "in_stock": True,
            "features": ["DVR", "4 cameras"],
        }
        power_doc = MagicMock()
        power_doc.id = "prod-anker-powerbank-26k"
        power_doc.to_dict.return_value = {
            "name": "Anker PowerCore 26800",
            "price": 32000,
            "currency": "NGN",
            "category": "accessories",
            "brand": "Anker",
            "in_stock": True,
            "features": ["PowerIQ"],
        }

        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter([cctv_doc, power_doc])
        mock_db.collection.return_value = mock_query

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
            result = await search_catalog(
                query="Which one do you have available?",
                category="security cameras",
            )

        products = result.get("products", [])
        assert products
        assert any("cctv" in str(item.get("name", "")).lower() for item in products)

    @pytest.mark.asyncio
    async def test_category_phrase_with_ambiguous_query_still_returns_security_options(self):
        """Ambiguous follow-up with security phrase should still return CCTV option."""
        from app.tools.catalog_tools import search_catalog

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=None):
            result = await search_catalog(
                query="Which one do you have available?",
                category="security cameras",
            )

        products = result.get("products", [])
        assert products
        assert any("cctv" in str(item.get("name", "")).lower() for item in products)

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

        with (
            patch("app.tools.catalog_tools._get_firestore_db", return_value=mock_db),
            patch("app.tools.catalog_tools.scoped_collection", return_value=mock_query),
        ):
            result = await search_catalog(query="iPhone 15")

        if result["products"]:
            product = result["products"][0]
            assert "name" in product
            assert "price" in product
            assert "in_stock" in product
