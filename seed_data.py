"""seed_data.py — Load industry configs into Firestore.

DEPRECATED: This script seeds legacy flat Firestore collections (industry_configs,
company_profiles, company_knowledge, products, booking_slots). After Phase 7 cutover,
use the registry CLI instead:

    python -m scripts.registry seed-templates --file=<template.json>
    python -m scripts.registry provision-company --tenant=X --company=Y --template=Z
    python -m scripts.registry import-knowledge --tenant=X --company=Y --file=<entries.json>

This file is retained for backward compatibility with pre-registry deployments.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from google.cloud import firestore

db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT", "ekaette"))

configs = {
    "electronics": {
        "name": "Electronics & Gadgets",
        "voice": "Aoede",
        "greeting": "Welcome! I can help you with device trade-ins, swaps, and purchases.",
        "rubric": {
            "categories": ["screen", "body", "battery", "functionality"],
            "scale": {"Excellent": 10, "Good": 7, "Fair": 5, "Poor": 2},
        },
        "pricing": {
            "iPhone 14 Pro": {"Excellent": 220000, "Good": 185000, "Fair": 140000, "Poor": 80000},
            "iPhone 15": {"Excellent": 280000, "Good": 240000, "Fair": 190000, "Poor": 120000},
            "Samsung S24": {"Excellent": 250000, "Good": 210000, "Fair": 165000, "Poor": 95000},
        },
    },
    "hotel": {
        "name": "Hotels & Hospitality",
        "voice": "Puck",
        "greeting": "Good day! Welcome to our hotel. How can I make your stay perfect?",
        "room_types": ["Standard", "Deluxe", "Ocean View", "Suite"],
        "pricing": {
            "Standard": 25000,
            "Deluxe": 45000,
            "Ocean View": 65000,
            "Suite": 120000,
        },
    },
    "automotive": {
        "name": "Automotive",
        "voice": "Charon",
        "greeting": "Hello! Looking to buy, sell, or service a vehicle?",
        "rubric": {
            "categories": ["body", "engine", "tires", "interior"],
            "scale": {"Excellent": 10, "Good": 7, "Fair": 5, "Poor": 2},
        },
    },
    "fashion": {
        "name": "Fashion & Retail",
        "voice": "Kore",
        "greeting": "Hey there! Let me help you find your perfect style.",
    },
}

booking_slots = [
    {
        "id": "slot-2026-03-01-10-ikeja",
        "date": "2026-03-01",
        "time": "10:00",
        "location": "Lagos - Ikeja",
        "available": True,
    },
    {
        "id": "slot-2026-03-01-14-ikeja",
        "date": "2026-03-01",
        "time": "14:00",
        "location": "Lagos - Ikeja",
        "available": True,
    },
    {
        "id": "slot-2026-03-02-11-wuse",
        "date": "2026-03-02",
        "time": "11:00",
        "location": "Abuja - Wuse",
        "available": True,
    },
]

products = [
    {
        "id": "prod-iphone-15-pro",
        "name": "iPhone 15 Pro",
        "price": 850000,
        "currency": "NGN",
        "category": "smartphones",
        "brand": "Apple",
        "in_stock": True,
        "features": ["A17 Pro chip", "48MP camera", "Titanium design"],
    },
    {
        "id": "prod-samsung-s24",
        "name": "Samsung Galaxy S24",
        "price": 620000,
        "currency": "NGN",
        "category": "smartphones",
        "brand": "Samsung",
        "in_stock": True,
        "features": ["Snapdragon 8 Gen 3", "120Hz AMOLED", "AI camera tools"],
    },
    {
        "id": "prod-google-pixel-8",
        "name": "Google Pixel 8",
        "price": 450000,
        "currency": "NGN",
        "category": "smartphones",
        "brand": "Google",
        "in_stock": False,
        "features": ["Tensor G3", "Best camera AI", "7 years updates"],
    },
]

company_profiles = [
    {
        "id": "ekaette-electronics",
        "industry": "electronics",
        "name": "Ekaette Devices Hub",
        "overview": "Trade-in focused electronics store serving Lagos and Abuja.",
        "facts": {
            "primary_location": "Lagos - Ikeja",
            "support_hours": "09:00-19:00",
            "pickup_window": "10:00-18:00",
        },
        "links": [
            "https://example.com/electronics",
            "https://example.com/electronics/policies",
        ],
        "system_connectors": {
            "crm": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_customer": {"loyalty_tier": "silver"},
                },
            }
        },
    },
    {
        "id": "ekaette-hotel",
        "industry": "hotel",
        "name": "Ekaette Grand Hotel",
        "overview": "Business and leisure hotel with concierge and airport pickup.",
        "facts": {
            "rooms": 120,
            "check_in_time": "14:00",
            "check_out_time": "12:00",
        },
        "links": [
            "https://example.com/hotel",
            "https://example.com/hotel/policies",
        ],
        "system_connectors": {
            "pms": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_booking": {"status": "confirmed", "room_type": "Deluxe"},
                },
            }
        },
    },
    {
        "id": "ekaette-automotive",
        "industry": "automotive",
        "name": "Ekaette Auto Exchange",
        "overview": "Vehicle trade, inspection, and maintenance booking center.",
        "facts": {
            "inspection_slots_per_day": 24,
            "service_hours": "08:00-18:00",
            "pickup_service": True,
        },
        "links": [
            "https://example.com/automotive",
            "https://example.com/automotive/inspection",
        ],
        "system_connectors": {
            "dms": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_vehicle": {"status": "available"},
                },
            }
        },
    },
    {
        "id": "ekaette-fashion",
        "industry": "fashion",
        "name": "Ekaette Style House",
        "overview": "Retail fashion outlet with in-store and virtual styling sessions.",
        "facts": {
            "branches": 3,
            "same_day_delivery_cutoff": "15:00",
            "return_window_days": 14,
        },
        "links": [
            "https://example.com/fashion",
            "https://example.com/fashion/returns",
        ],
        "system_connectors": {
            "erp": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_order": {"status": "processing"},
                },
            }
        },
    },
]

company_knowledge = [
    {
        "id": "kb-elec-hours",
        "company_id": "ekaette-electronics",
        "title": "Support hours",
        "text": "Customer support is available daily from 9 AM to 7 PM.",
        "tags": ["support", "hours"],
        "source": "seed",
    },
    {
        "id": "kb-elec-pickup",
        "company_id": "ekaette-electronics",
        "title": "Pickup policy",
        "text": "Same-day pickup is available for confirmed bookings made before 2 PM.",
        "tags": ["pickup", "policy"],
        "source": "seed",
    },
    {
        "id": "kb-hotel-checkout",
        "company_id": "ekaette-hotel",
        "title": "Late checkout policy",
        "text": "Late checkout until 1 PM is available for premium guests.",
        "tags": ["checkout", "policy"],
        "source": "seed",
    },
    {
        "id": "kb-hotel-breakfast",
        "company_id": "ekaette-hotel",
        "title": "Breakfast schedule",
        "text": "Breakfast is served from 6:30 AM to 10:30 AM daily.",
        "tags": ["breakfast", "amenities"],
        "source": "seed",
    },
    {
        "id": "kb-auto-inspection",
        "company_id": "ekaette-automotive",
        "title": "Inspection checklist",
        "text": "Vehicle inspections cover engine, brakes, tires, electronics, and body condition.",
        "tags": ["inspection", "service"],
        "source": "seed",
    },
    {
        "id": "kb-auto-finance",
        "company_id": "ekaette-automotive",
        "title": "Financing support",
        "text": "Financing options are available through partner banks for qualified buyers.",
        "tags": ["finance", "sales"],
        "source": "seed",
    },
    {
        "id": "kb-fashion-returns",
        "company_id": "ekaette-fashion",
        "title": "Return policy",
        "text": "Returns are accepted within 14 days for unworn items with tags attached.",
        "tags": ["returns", "policy"],
        "source": "seed",
    },
    {
        "id": "kb-fashion-sizing",
        "company_id": "ekaette-fashion",
        "title": "Sizing assistance",
        "text": "Virtual stylists can help with sizing and fit recommendations over chat.",
        "tags": ["sizing", "styling"],
        "source": "seed",
    },
]

if __name__ == "__main__":
    for industry_id, config in configs.items():
        db.collection("industry_configs").document(industry_id).set(config)
        print(f"Loaded {industry_id}")

    for slot in booking_slots:
        db.collection("booking_slots").document(slot["id"]).set(slot)
    print(f"Loaded {len(booking_slots)} booking slots")

    for product in products:
        db.collection("products").document(product["id"]).set(product)
    print(f"Loaded {len(products)} products")

    for profile in company_profiles:
        doc = dict(profile)
        profile_id = doc.pop("id")
        db.collection("company_profiles").document(profile_id).set(doc)
    print(f"Loaded {len(company_profiles)} company profiles")

    for entry in company_knowledge:
        doc = dict(entry)
        knowledge_id = doc.pop("id")
        db.collection("company_knowledge").document(knowledge_id).set(doc)
    print(f"Loaded {len(company_knowledge)} company knowledge entries")

    print("All seed data loaded!")
