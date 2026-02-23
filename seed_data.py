"""seed_data.py — Load industry configs into Firestore."""

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

if __name__ == "__main__":
    for industry_id, config in configs.items():
        db.collection("industry_configs").document(industry_id).set(config)
        print(f"Loaded {industry_id}")

    print("All industry configs loaded!")
