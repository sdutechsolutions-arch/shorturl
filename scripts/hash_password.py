"""Generate a bcrypt hash for an admin password.

Usage: python scripts/hash_password.py
       (prompts for password; echoes the hash)
"""
import getpass
import sys

from app.auth import hash_password


def main() -> None:
    p1 = getpass.getpass("New admin password: ")
    p2 = getpass.getpass("Repeat password: ")
    if p1 != p2:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(p1) < 8:
        print("Password too short (min 8 chars).", file=sys.stderr)
        sys.exit(1)
    print(hash_password(p1))


if __name__ == "__main__":
    main()
