import sys
try:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")
    h = pwd_context.hash("password123")
    print("Hash:", h)
    v = pwd_context.verify("password123", h)
    print("Verify:", v)
except Exception as e:
    print("Error:", type(e).__name__, str(e))
