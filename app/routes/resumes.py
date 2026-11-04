import os
import re
import uuid
import zipfile

import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.config import JWT_SECRET, MAX_RESUME_MB, UPLOAD_DIR
from app.database import (
    applications_collection,
    resumes_collection,
    skills_collection,
    user_profiles_collection,
)
from app.dependencies.auth import get_current_user
from app.utils import get_or_404, now, oid, ser

router = APIRouter()

RESUME_DIR = UPLOAD_DIR / "resumes"
RESUME_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAGIC = {".pdf": b"%PDF", ".docx": b"PK", ".doc": b"\xd0\xcf\x11\xe0"}


def _shape(resume: dict) -> dict:
    item = ser(resume)
    item["name"] = resume.get("original_name") or os.path.basename(resume.get("file_url", ""))
    return item


@router.post("", status_code=201)
async def upload_resume(file: UploadFile = File(...), user=Depends(get_current_user)):
    original = os.path.basename(file.filename or "resume")
    extension = os.path.splitext(original)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only PDF, DOC and DOCX files are allowed")
    content = await file.read()
    if not content:
        raise HTTPException(400, "The file is empty")
    if len(content) > MAX_RESUME_MB * 1024 * 1024:
        raise HTTPException(400, f"File is too large (max {MAX_RESUME_MB} MB)")
    if not content.startswith(MAGIC[extension]):
        raise HTTPException(400, f"The file content does not look like a valid {extension.upper().strip('.')} file")

    filename = f"{uuid.uuid4()}{extension}"
    with open(RESUME_DIR / filename, "wb") as buffer:
        buffer.write(content)

    first = resumes_collection.count_documents({"user_id": user["_id"]}) == 0
    ts = now()
    doc = {
        "user_id": user["_id"],
        "file_url": f"/uploads/resumes/{filename}",
        "original_name": original[:150],
        "parsed_data": {},
        "ai_score": None,
        "suggestions": [],
        "is_default": first,
        "created_at": ts,
        "updated_at": ts,
    }
    result = resumes_collection.insert_one(doc)
    if first:
        user_profiles_collection.update_one({"user_id": user["_id"]}, {"$set": {"resume_id": result.inserted_id}})
    return {
        "message": "Resume uploaded successfully",
        "resume_id": str(result.inserted_id),
        "file_url": doc["file_url"],
        "is_default": first,
    }


@router.get("")
def my_resumes(user=Depends(get_current_user)):
    rows = resumes_collection.find({"user_id": user["_id"]}).sort("created_at", -1)
    return [_shape(r) for r in rows]


@router.get("/{resume_id}")
def get_resume(resume_id: str, user=Depends(get_current_user)):
    return _shape(get_or_404(resumes_collection, oid(resume_id, "resume ID"), "Resume", {"user_id": user["_id"]}))


@router.delete("/{resume_id}")
def delete_resume(resume_id: str, user=Depends(get_current_user)):
    resume = get_or_404(resumes_collection, oid(resume_id, "resume ID"), "Resume", {"user_id": user["_id"]})
    resumes_collection.delete_one({"_id": resume["_id"]})
    path = RESUME_DIR / os.path.basename(resume.get("file_url", ""))
    if path.is_file():
        path.unlink()
    # keep applications intact but detach the file
    applications_collection.update_many({"resume_id": resume["_id"]}, {"$set": {"resume_id": None}})
    if resume.get("is_default"):
        nxt = resumes_collection.find_one({"user_id": user["_id"]})
        if nxt:
            resumes_collection.update_one({"_id": nxt["_id"]}, {"$set": {"is_default": True}})
        user_profiles_collection.update_one(
            {"user_id": user["_id"]}, {"$set": {"resume_id": nxt["_id"] if nxt else None}}
        )
    return {"message": "Resume deleted successfully"}


@router.patch("/{resume_id}/default")
def set_default(resume_id: str, user=Depends(get_current_user)):
    resume = get_or_404(resumes_collection, oid(resume_id, "resume ID"), "Resume", {"user_id": user["_id"]})
    resumes_collection.update_many({"user_id": user["_id"]}, {"$set": {"is_default": False}})
    resumes_collection.update_one({"_id": resume["_id"]}, {"$set": {"is_default": True, "updated_at": now()}})
    user_profiles_collection.update_one({"user_id": user["_id"]}, {"$set": {"resume_id": resume["_id"]}})
    return {"message": "Default resume updated successfully"}


# ---------------------------------------------------------------- analysis


def extract_text(path, extension: str) -> str:
    try:
        if extension == ".pdf":
            from pypdf import PdfReader

            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        if extension == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)
    except Exception:
        return ""
    return ""


SECTIONS = {
    "summary": r"\b(summary|objective|profile|about me)\b",
    "experience": r"\b(experience|employment|work history)\b",
    "education": r"\b(education|university|college|b\.?tech|bachelor|master|degree)\b",
    "skills": r"\b(skills|technologies|tech stack|competenc)",
    "projects": r"\b(projects?|portfolio)\b",
}
ACTION_VERBS = (
    "built developed designed led managed created implemented improved optimized launched delivered "
    "reduced increased automated migrated mentored architected"
).split()


def analyze_text(text: str, known_skills: list, profile_skills: list) -> dict:
    lower = text.lower()
    words = re.findall(r"[A-Za-z][A-Za-z+#.\-]*", text)
    wc = len(words)
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phones = re.findall(r"(?:\+?\d[\d\s\-()]{8,}\d)", text)
    sections = [name for name, pat in SECTIONS.items() if re.search(pat, lower)]
    found_skills = sorted({s for s in known_skills if re.search(r"(?<![A-Za-z])" + re.escape(s.lower()) + r"(?![A-Za-z])", lower)})
    verbs = [v for v in ACTION_VERBS if v in lower]
    metrics = re.findall(r"\b\d+(?:\.\d+)?\s?(?:%|x|\+|k\b|lpa|users|customers)", lower)
    links = {"github": "github.com" in lower, "linkedin": "linkedin.com" in lower}

    score, suggestions = 0, []
    # contact (15)
    if emails:
        score += 8
    else:
        suggestions.append("Add a professional email address at the top of your resume.")
    if phones:
        score += 7
    else:
        suggestions.append("Add a phone number so recruiters can reach you quickly.")
    # sections (30)
    score += min(30, len(sections) * 6)
    for missing in [s for s in ("experience", "education", "skills") if s not in sections]:
        suggestions.append(f"Add a clear '{missing.title()}' section.")
    # length (15)
    if 200 <= wc <= 900:
        score += 15
    elif wc >= 100:
        score += 8
        suggestions.append("Aim for 200-800 words: " + ("add more detail." if wc < 200 else "trim older or minor details."))
    else:
        suggestions.append("Your resume has very little readable text - make sure it is not a scanned image.")
    # skills (20)
    score += min(20, len(found_skills) * 4)
    if len(found_skills) < 4:
        suggestions.append("List more relevant technical skills (frameworks, tools, languages).")
    missing_profile = [s for s in profile_skills if s.lower() not in lower]
    if missing_profile:
        suggestions.append("Your profile lists skills that are not in this resume: " + ", ".join(missing_profile[:5]) + ".")
    # impact (20)
    score += min(10, len(verbs) * 2)
    score += min(10, len(metrics) * 5)
    if not metrics:
        suggestions.append("Quantify achievements (e.g. 'reduced load time by 40%').")
    if len(verbs) < 3:
        suggestions.append("Start bullet points with strong action verbs (built, led, optimized...).")
    if not links["linkedin"] and not links["github"]:
        suggestions.append("Add your LinkedIn or GitHub link.")
    return {
        "ai_score": float(min(100, score)),
        "suggestions": suggestions,
        "parsed_data": {
            "word_count": wc,
            "emails": emails[:3],
            "phones": [p.strip() for p in phones[:2]],
            "sections": sections,
            "skills_found": found_skills,
            "has_github": links["github"],
            "has_linkedin": links["linkedin"],
        },
    }


@router.post("/{resume_id}/analyze")
def analyze_resume(resume_id: str, user=Depends(get_current_user)):
    resume = get_or_404(resumes_collection, oid(resume_id, "resume ID"), "Resume", {"user_id": user["_id"]})
    path = RESUME_DIR / os.path.basename(resume.get("file_url", ""))
    if not path.is_file():
        raise HTTPException(404, "Resume file is missing on the server")
    text = extract_text(path, path.suffix.lower())
    if not text.strip():
        raise HTTPException(422, "Could not read any text from this file (scanned image or unsupported .doc). Upload a text PDF or DOCX.")
    known = [s["name"] for s in skills_collection.find({}, {"name": 1})]
    profile = user_profiles_collection.find_one({"user_id": user["_id"]}) or {}
    result = analyze_text(text, known, profile.get("skills", []))
    resumes_collection.update_one({"_id": resume["_id"]}, {"$set": {**result, "updated_at": now()}})
    return {"message": "Analysis complete", **result}


# ------------------------------------------------------------ private downloads (signed, short-lived)
LINK_SECONDS = 300


def _sig(resume_id: str, exp: int) -> str:
    return hmac.new(JWT_SECRET.encode(), f"resume:{resume_id}:{exp}".encode(), hashlib.sha256).hexdigest()


@router.get("/{resume_id}/link")
def resume_link(resume_id: str, user=Depends(get_current_user)):
    """Resumes are private: only the owner, admins and the recruiter the candidate applied to can open them."""
    rid = oid(resume_id, "resume ID")
    resume = get_or_404(resumes_collection, rid, "Resume")
    allowed = resume["user_id"] == user["_id"] or user["role"] == "admin"
    if not allowed and user["role"] == "recruiter":
        allowed = applications_collection.count_documents({"resume_id": rid, "recruiter_id": user["_id"]}) > 0
    if not allowed:
        raise HTTPException(404, "Resume not found")
    exp = int(time.time()) + LINK_SECONDS
    return {"url": f"/api/files/resume/{resume_id}?exp={exp}&sig={_sig(resume_id, exp)}", "expires_in": LINK_SECONDS}


files_router = APIRouter()


@files_router.get("/resume/{resume_id}")
def resume_file(resume_id: str, exp: int = Query(...), sig: str = Query(..., max_length=128)):
    if exp < time.time() or not hmac.compare_digest(sig, _sig(resume_id, exp)):
        raise HTTPException(403, "This download link is invalid or has expired")
    resume = get_or_404(resumes_collection, oid(resume_id, "resume ID"), "Resume")
    path = RESUME_DIR / os.path.basename(resume.get("file_url", ""))
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=resume.get("original_name") or path.name, headers={"Cache-Control": "private, no-store"})
