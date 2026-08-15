"""Loads /backend/data/product_requirements.json and exposes helpers."""
import json
import os

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "product_requirements.json")

_catalog_cache = None


def load_catalog():
    global _catalog_cache
    if _catalog_cache is None:
        with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
            _catalog_cache = json.load(f)
    return _catalog_cache


def get_product(product_id: str):
    catalog = load_catalog()
    if product_id not in catalog:
        raise KeyError(f"Unknown product_id: {product_id}")
    return catalog[product_id]


def list_products():
    return load_catalog()
