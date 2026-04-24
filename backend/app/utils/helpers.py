from app.db.mongo import emails_collection

def generate_internal_id():
    count = emails_collection.count_documents({})
    return f"INT-{count + 1:03d}"