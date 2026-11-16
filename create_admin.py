"""Create the first admin account (admins cannot self-register through the API).
Usage:  python create_admin.py --email you@example.com --name "Your Name"
The password is typed at a hidden prompt, never stored in code or shell history."""
import argparse
import getpass
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Role, User
from app.schemas import strong_password
from app.security import hash_password

parser = argparse.ArgumentParser()
parser.add_argument("--email", required=True)
parser.add_argument("--name", required=True)
args = parser.parse_args()

password = getpass.getpass("Password: ")
if password != getpass.getpass("Repeat password: "):
    sys.exit("Passwords do not match.")
try:
    strong_password(password)
except ValueError as e:
    sys.exit(str(e))

with SessionLocal() as db:
    if db.scalar(select(User.id).where(User.email == args.email.lower())):
        sys.exit("A user with this email already exists.")
    db.add(User(email=args.email.lower(), full_name=args.name, role=Role.admin,
                hashed_password=hash_password(password)))
    db.commit()
print(f"Admin {args.email} created.")
