"""Database backups: JSON dumps in BACKUP_DIR, retention, restore. Never exposes dumps over HTTP (they hold password hashes)."""
import gzip
import json
import os
import re
import uuid
from datetime import timedelta

from bson import json_util

from app import config
from app.database import backups_collection, db, settings_collection
from app.utils import now

SKIP = {"backups", "health_checks", "system_errors"}  # operational collections are not part of a dump
SCHEDULE_KEY = "backup_schedule"
DEFAULT_SCHEDULE = {"enabled": False, "frequency": "daily", "keep": 7}


def schedule() -> dict:
    doc = settings_collection.find_one({"key": SCHEDULE_KEY}) or {}
    return {**DEFAULT_SCHEDULE, **{k: doc[k] for k in DEFAULT_SCHEDULE if k in doc}, "last_run_at": doc.get("last_run_at")}


def set_schedule(values: dict) -> dict:
    settings_collection.update_one({"key": SCHEDULE_KEY}, {"$set": {**values, "updated_at": now()}}, upsert=True)
    return schedule()


def create(kind: str = "manual", by: str = "system") -> dict:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = now()
    name = f"carevo-{ts:%Y%m%d-%H%M%S}-{kind}-{uuid.uuid4().hex[:6]}.json.gz"
    row = {"filename": name, "kind": kind, "by": by, "status": "running", "created_at": ts}
    row["_id"] = backups_collection.insert_one(dict(row)).inserted_id
    try:
        counts, data = {}, {}
        for col in sorted(db.list_collection_names()):
            if col in SKIP or col.startswith("system."):
                continue
            docs = list(db[col].find({}))
            data[col] = docs
            counts[col] = len(docs)
        payload = json_util.dumps({"version": 1, "created_at": ts, "collections": data}).encode()
        path = config.BACKUP_DIR / name
        with gzip.open(path, "wb") as f:
            f.write(payload)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        backups_collection.update_one({"_id": row["_id"]}, {"$set": {"status": "success", "size": path.stat().st_size, "collections": len(counts), "documents": sum(counts.values())}})
    except Exception as exc:  # noqa: BLE001
        backups_collection.update_one({"_id": row["_id"]}, {"$set": {"status": "failed", "error": str(exc)[:300]}})
    out = backups_collection.find_one({"_id": row["_id"]})
    if kind != "pre-restore":  # never delete the backup that is about to be restored
        prune()
    return out


def prune() -> None:
    keep = schedule()["keep"]
    rows = list(backups_collection.find({"status": "success"}).sort("created_at", -1))
    for r in rows[keep:]:
        delete(r)


def delete(row: dict) -> None:
    p = config.BACKUP_DIR / os.path.basename(row.get("filename", ""))
    if p.is_file():
        p.unlink()
    backups_collection.delete_one({"_id": row["_id"]})


def restore(row: dict) -> dict:
    p = config.BACKUP_DIR / os.path.basename(row["filename"])
    if not re.fullmatch(r"[\w.\-]+", p.name) or not p.is_file():
        raise FileNotFoundError("Backup file is missing on the server")
    with gzip.open(p, "rb") as f:
        data = json_util.loads(f.read().decode())
    cols = data.get("collections") or {}
    for name, docs in cols.items():  # validate the whole file before touching anything
        if not isinstance(docs, list) or name in SKIP or name.startswith("system."):
            raise ValueError("Backup file is invalid")
    total = 0
    for name, docs in cols.items():
        db[name].delete_many({})
        if docs:
            db[name].insert_many(docs)
        total += len(docs)
    return {"collections": len(cols), "documents": total}


def run_if_due() -> bool:
    s = schedule()
    if not s["enabled"]:
        return False
    last = s.get("last_run_at")
    if last and last.tzinfo is None:
        from datetime import timezone

        last = last.replace(tzinfo=timezone.utc)
    gap = timedelta(days=7 if s["frequency"] == "weekly" else 1)
    if last and now() - last < gap:
        return False
    set_schedule({"last_run_at": now()})
    create("scheduled")
    return True
