from fastapi import APIRouter, Request
from app.db.mongo import failed_jobs_collection
from bson import ObjectId
from app.services.auth_service import require_admin

router = APIRouter()

# ✅ Get all failed jobs
@router.get("/api/failed-jobs")
def get_failed_jobs():
    jobs = list(
        failed_jobs_collection.find({}, {"_id": 1, "type": 1, "retry_count": 1, "status": 1, "error": 1})
        .sort("created_at", -1)
    )

    # convert ObjectId to string
    for j in jobs:
        j["_id"] = str(j["_id"])

    return jobs


# ✅ Manual retry API
@router.post("/api/retry-job/{job_id}")
def retry_job(job_id: str, request: Request):
    require_admin(request)

    job = failed_jobs_collection.find_one({"_id": ObjectId(job_id)})

    if not job:
        return {"message": "Job not found"}

    failed_jobs_collection.update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"status": "pending"}}
    )

    return {"message": "Retry triggered"}
