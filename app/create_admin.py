"""Create or reset an admin account.

    python -m app.create_admin admin@carevo.com "MyStrong@123" "Sudhir"

If the email exists, its password is reset, the role becomes admin and the account is re-activated.
"""
import sys

from app.database import ensure_indexes, user_profiles_collection, users_collection
from app.models.users import create_profile_document, create_user_document
from app.services.auth import hash_password, verify_password
from app.utils import now


def main() -> int:
    if len(sys.argv) < 3:
        print('usage: python -m app.create_admin <email> <password> [name]')
        return 2
    email, password = sys.argv[1].strip().lower(), sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else 'Admin'
    if len(password) < 8:
        print('password must be at least 8 characters')
        return 2
    ensure_indexes()
    hashed = hash_password(password)
    existing = users_collection.find_one({'email': email})
    if existing:
        users_collection.update_one({'_id': existing['_id']}, {'$set': {'password_hash': hashed, 'role': 'admin', 'is_active': True, 'updated_at': now()}})
        print(f'Updated existing account {email}: role=admin, password reset.')
    else:
        doc = create_user_document(name, email, hashed, role='admin')
        uid = users_collection.insert_one(doc).inserted_id
        user_profiles_collection.insert_one(create_profile_document(uid))
        print(f'Created admin {email}.')
    ok = verify_password(password, users_collection.find_one({'email': email})['password_hash'])
    print('Password check:', 'OK - you can log in now' if ok else 'FAILED - install pwdlib: pip install "pwdlib[argon2]"')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
