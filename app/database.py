from pymongo import MongoClient

from app.config import MONGO_URI, DATABASE_NAME


client = MongoClient(MONGO_URI)

db = client[DATABASE_NAME]


# Collections

users_collection = db["users"]

user_profiles_collection = db["user_profiles"]

resumes_collection = db["resumes"]

skills_collection = db["skills"]

skill_categories_collection = db["skill_categories"]

user_skills_collection = db["user_skills"]

jobs_collection = db["jobs"]

applications_collection = db["applications"]

saved_jobs_collection = db["saved_jobs"]