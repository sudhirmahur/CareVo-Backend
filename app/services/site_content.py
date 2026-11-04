"""Landing / login page content that admins can edit (stored in one document, merged over defaults)."""
import copy

from app.database import (
    applications_collection,
    companies_collection,
    jobs_collection,
    site_content_collection,
    users_collection,
)
from app.utils import now

LOGO_DEFAULT = ""

DEFAULTS = {
    "brand": {"name": "CareVo", "tagline": "Find your next opportunity with CareVo. Connecting talent with top companies.", "logo_url": ""},
    "nav": {
        "links": [
            {"label": "Home", "href": "/"},
            {"label": "Jobs", "href": "/jobs"},
            {"label": "Companies", "href": "/companies"},
            {"label": "Resources", "href": "/#resources"},
            {"label": "About", "href": "/#how-it-works"},
        ],
        "login_label": "Login",
        "signup_label": "Sign Up",
        "employer_label": "For Employers",
        "employer_href": "/register?as=recruiter",
    },
    "hero": {
        "badge": "Your Career, Our Priority",
        "title": "Find Your Next Opportunity with CareVo",
        "highlight": "CareVo",
        "subtitle": "Discover the best job opportunities, connect with top companies, and take the next step in your career journey.",
        "search_job_placeholder": "Job title, skills or keyword",
        "search_location_placeholder": "Location",
        "search_button": "Search Jobs",
        "image_url": "/landing/hero.webp",
    },
    "stats": {
        "items": [
            {"icon": "briefcase", "label": "Active Jobs", "value": "10K+", "source": "manual"},
            {"icon": "building", "label": "Top Companies", "value": "5K+", "source": "manual"},
            {"icon": "users", "label": "Job Seekers", "value": "50K+", "source": "manual"},
            {"icon": "trendUp", "label": "Success Rate", "value": "95%", "source": "manual"},
        ]
    },
    "categories": {
        "title": "Popular Job Categories",
        "view_all_label": "View All",
        "items": [
            {"name": "Technology", "count_label": "20K+ Jobs", "icon": "layers", "color": "blue", "query": "technology"},
            {"name": "Marketing", "count_label": "8K+ Jobs", "icon": "send", "color": "red", "query": "marketing"},
            {"name": "Design", "count_label": "6K+ Jobs", "icon": "sparkles", "color": "purple", "query": "design"},
            {"name": "Sales", "count_label": "5K+ Jobs", "icon": "chart", "color": "green", "query": "sales"},
            {"name": "HR", "count_label": "3K+ Jobs", "icon": "users", "color": "orange", "query": "hr"},
            {"name": "Finance", "count_label": "4K+ Jobs", "icon": "dollar", "color": "indigo", "query": "finance"},
            {"name": "Operations", "count_label": "5K+ Jobs", "icon": "settings", "color": "pink", "query": "operations"},
        ],
    },
    "trusted": {
        "title": "Trusted by Leading Companies",
        "highlight": "Companies",
        "subtitle": "Thousands of companies hire talent through CareVo",
        "items": [{"name": n, "logo_url": f"/landing/logos/{n.lower()}.png"} for n in ["Google", "Microsoft", "Amazon", "Meta", "Adobe", "Infosys", "TCS", "Accenture", "Upstox"]],
    },
    "how": {
        "title": "How CareVo Works",
        "highlight": "Vo",
        "subtitle": "Get hired in just a few simple steps and start your career journey with the right opportunities.",
        "cta_label": "Get Started Now",
        "items": [
            {"icon": "user", "title": "Create Your Profile", "text": "Build a professional profile and showcase your skills"},
            {"icon": "file", "title": "Explore Jobs", "text": "Find the best job opportunities from top companies"},
            {"icon": "send", "title": "Apply Easily", "text": "Apply to multiple jobs with one click"},
            {"icon": "briefcase", "title": "Get Hired", "text": "Connect with employers and start your journey"},
        ],
    },
    "promo": {"text": "Your Dream Job is Waiting!", "image_url": "/landing/promo.webp"},
    "testimonials": {
        "title": "What Our Users Say",
        "highlight": "Say",
        "subtitle": "Real stories from people who found their dream jobs with CareVo.",
        "items": [
            {"name": "Priya Sharma", "role": "Frontend Developer", "rating": 5, "quote": "CareVo made my job search so easy. I got multiple interview calls within a week!", "avatar_url": "/landing/avatar-1.webp"},
            {"name": "Rahul Verma", "role": "Data Analyst", "rating": 5, "quote": "Amazing platform! I found my dream job at a top company through CareVo.", "avatar_url": "/landing/avatar-2.webp"},
            {"name": "Neha Singh", "role": "Product Designer", "rating": 5, "quote": "The best job portal I've used. Simple, clean and actually helpful!", "avatar_url": "/landing/avatar-3.webp"},
        ],
    },
    "resources": {
        "title": "Career Resources",
        "highlight": "Resources",
        "subtitle": "Get tips, guides and insights to grow your career.",
        "cta_label": "View All Articles",
        "cta_href": "",
        "items": [
            {"title": "10 Tips to Ace Your Next Interview", "tag": "Career Tips", "date": "Oct 25, 2024", "image_url": "/landing/article-1.webp", "href": ""},
            {"title": "How to Build a Standout Resume", "tag": "Resume", "date": "Oct 20, 2024", "image_url": "/landing/article-2.webp", "href": ""},
            {"title": "Top Skills in Demand in 2024", "tag": "Industry Insights", "date": "Oct 18, 2024", "image_url": "/landing/article-3.webp", "href": ""},
        ],
    },
    "cta": {"title": "Ready to Start Your Career Journey?", "subtitle": "Join thousands of job seekers and find the right opportunity with CareVo.", "button_label": "Sign Up for Free", "button_href": "/register"},
    "footer": {
        "tagline": "Find your next opportunity with CareVo. Connecting talent with top companies.",
        "columns": [
            {"title": "Quick Links", "links": [{"label": "Home", "href": "/"}, {"label": "Jobs", "href": "/jobs"}, {"label": "Companies", "href": "/companies"}, {"label": "Resources", "href": "/#resources"}, {"label": "About Us", "href": "/#how-it-works"}]},
            {"title": "For Job Seekers", "links": [{"label": "Create Profile", "href": "/register"}, {"label": "Browse Jobs", "href": "/jobs"}, {"label": "Career Tips", "href": "/#resources"}, {"label": "Resume Builder", "href": "/resume-builder"}, {"label": "Help Center", "href": "mailto:support@carevo.app"}]},
            {"title": "For Employers", "links": [{"label": "Post a Job", "href": "/register?as=recruiter"}, {"label": "Find Talent", "href": "/register?as=recruiter"}, {"label": "Pricing", "href": "/#cta"}, {"label": "Employer Resources", "href": "/#resources"}, {"label": "Contact Sales", "href": "mailto:support@carevo.app"}]},
        ],
        "socials": [],
        "newsletter_title": "Newsletter",
        "newsletter_text": "Get the latest job updates and career tips.",
        "newsletter_button": "Subscribe",
        "copyright": "© 2026 CareVo. All rights reserved.",
        "legal_links": [{"label": "Privacy Policy", "href": "/privacy"}, {"label": "Terms of Service", "href": "/terms"}, {"label": "Contact Us", "href": "mailto:support@carevo.app"}],
    },
    "auth_page": {
        "badge": "Your Career, Our Priority",
        "title": "Build a Better Tomorrow with CareVo",
        "highlight": "CareVo",
        "subtitle": "Discover top job opportunities, connect with leading companies, and grow your career with the right guidance.",
        "features": [
            {"icon": "briefcase", "title": "Explore thousands of jobs", "text": "Find the right opportunity for you"},
            {"icon": "building", "title": "Connect with top companies", "text": "Work with leading brands"},
            {"icon": "trendUp", "title": "Get career guidance", "text": "Resources and tools to grow"},
            {"icon": "shield", "title": "Safe and trusted platform", "text": "Your data is always secure"},
        ],
        "quote": "Jobs are not just about work, they're about a better future.",
        "quote_author": "CareVo",
        "mobile_quote": "Your Dream Job is Just a Login Away",
    },
    "seo": {"title": "CareVo - Find Your Next Opportunity", "description": "Discover the best job opportunities, connect with top companies and apply with one click."},
    "legal": {
        "terms": "These Terms of Service govern your use of CareVo. By creating an account you agree to use the platform lawfully, to provide accurate information, and not to post misleading jobs or abusive content. Administrators may remove content or suspend accounts that violate these rules.\n\n(Edit this text from Admin > Landing page > Legal.)",
        "privacy": "CareVo stores the information you provide (profile, resume, applications, messages) to run the service. We do not sell your personal data. You can ask an administrator to delete your account and data at any time.\n\n(Edit this text from Admin > Landing page > Legal.)",
    },
}


def get_content() -> dict:
    """Defaults with any stored sections laid over them."""
    doc = site_content_collection.find_one({"key": "landing"}) or {}
    out = copy.deepcopy(DEFAULTS)
    for k, v in (doc.get("content") or {}).items():
        if k in out and isinstance(v, dict):
            out[k] = v
    return out


def save_sections(sections: dict, admin_id) -> dict:
    doc = site_content_collection.find_one({"key": "landing"}) or {}
    content = dict(doc.get("content") or {})
    content.update(sections)
    site_content_collection.update_one(
        {"key": "landing"}, {"$set": {"content": content, "updated_at": now(), "updated_by": admin_id}}, upsert=True
    )
    return get_content()


def reset_sections(names: list[str], admin_id) -> dict:
    doc = site_content_collection.find_one({"key": "landing"}) or {}
    content = {k: v for k, v in (doc.get("content") or {}).items() if k not in names}
    site_content_collection.update_one(
        {"key": "landing"}, {"$set": {"content": content, "updated_at": now(), "updated_by": admin_id}}, upsert=True
    )
    return get_content()


def live_stats() -> dict:
    return {
        "jobs": jobs_collection.count_documents({"status": "approved", "is_active": True}),
        "companies": companies_collection.count_documents({"status": "approved"}),
        "seekers": users_collection.count_documents({"role": "user", "is_active": True}),
        "recruiters": users_collection.count_documents({"role": "recruiter", "is_active": True}),
        "applications": applications_collection.count_documents({}),
    }
