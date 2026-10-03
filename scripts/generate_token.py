import sys
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


def generate_token(user_id: str, role: str) -> str:

    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=2),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


if __name__ == "__main__":

    user_id = sys.argv[1] if len(sys.argv) > 1 else "user-001"
    role = sys.argv[2] if len(sys.argv) > 2 else "user"

    token = generate_token(user_id, role)

    print(token)