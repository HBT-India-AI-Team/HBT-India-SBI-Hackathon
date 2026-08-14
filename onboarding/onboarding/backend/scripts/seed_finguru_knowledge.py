"""
Seeds the FinGuruTopic table from the JSON files in
/backend/data/finguru_knowledge/ (fin_wiki.json, products.json,
govt_schemes.json).

Idempotent: upserts by the explicit stable `id` slug in each entry, so
re-running updates existing rows rather than creating duplicates.

Every entry is loaded with needs_review=True -- these were populated via web
research (see each entry's source_url) and carry the same "needs stakeholder
review" disclaimer as product_requirements.json. Run with:
    python -m backend.scripts.seed_finguru_knowledge
"""
import glob
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.models.db import SessionLocal, Base, engine
from backend.models import models as m

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "finguru_knowledge")


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def load_entries():
    entries = []
    for path in sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a JSON list of topics")
        entries.extend(data)
    return entries


def main():
    Base.metadata.create_all(bind=engine)  # safety net if init_db wasn't run
    entries = load_entries()
    db = SessionLocal()
    created, updated = 0, 0
    try:
        for e in entries:
            topic_id = e["id"]
            existing = db.query(m.FinGuruTopic).filter_by(id=topic_id).first()
            fields = dict(
                category=e["category"],
                title=e["title"],
                tags=e.get("tags", []),
                summary=e.get("summary"),
                body=e.get("body"),
                source_url=e.get("source_url"),
                eligibility_tags=e.get("eligibility_tags", []),
                needs_review=True,
                last_verified_at=_parse_date(e.get("last_verified_at")),
            )
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
                updated += 1
            else:
                db.add(m.FinGuruTopic(id=topic_id, **fields))
                created += 1
        db.commit()
    finally:
        db.close()

    by_cat = {}
    db = SessionLocal()
    try:
        for t in db.query(m.FinGuruTopic).all():
            by_cat[t.category] = by_cat.get(t.category, 0) + 1
    finally:
        db.close()
    print(f"[seed_finguru_knowledge] created={created} updated={updated} total_by_category={by_cat}")


if __name__ == "__main__":
    main()
