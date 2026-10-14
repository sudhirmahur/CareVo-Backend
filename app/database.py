from pymongo import MongoClient
from app.config import MONGO_URI, DATABASE_NAME

client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=5000
)

db = client[DATABASE_NAME]

# Check database connection
try:
    client.admin.command("ping")
    print("MongoDB Connected Successfully!")
except Exception as e:
    print("MongoDB Connection Error:", repr(e))

users_collection = db["users"]
user_profiles_collection = db["user_profiles"]
resumes_collection = db["resumes"]
skills_collection = db["skills"]
skill_categories_collection = db["skill_categories"]
user_skills_collection = db["user_skills"]
jobs_collection = db["jobs"]
applications_collection = db["applications"]
saved_jobs_collection = db["saved_jobs"]