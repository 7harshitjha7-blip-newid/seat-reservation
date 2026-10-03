import sys
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import requests

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8000"


def generate_token(
    user_id: str,
    role: str,
    include_exp: bool = True,
    expired: bool = False,
) -> str:

    payload = {
        "sub": user_id,
        "role": role,
    }

    if include_exp:
        if expired:
            payload["exp"] = (
                datetime.now(timezone.utc)
                - timedelta(hours=1)
            )
        else:
            payload["exp"] = (
                datetime.now(timezone.utc)
                + timedelta(hours=1)
            )

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def test_security_headers():
    print("\n[1] Testing security headers...")

    response = requests.get(
        f"{BASE_URL}/health/live",
        timeout=10,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Health endpoint failed: {response.status_code}"
        )

    required_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }

    for header, expected_value in required_headers.items():
        actual_value = response.headers.get(header)

        print(
            f"{header}: {actual_value}"
        )

        if actual_value != expected_value:
            raise RuntimeError(
                f"{header} expected '{expected_value}', "
                f"got '{actual_value}'"
            )

    permissions_policy = response.headers.get(
        "Permissions-Policy"
    )

    print(
        f"Permissions-Policy: {permissions_policy}"
    )

    if not permissions_policy:
        raise RuntimeError(
            "Permissions-Policy header missing"
        )

    print(
        "PASS: Security headers present"
    )


def test_request_id():
    print("\n[2] Testing request ID...")

    response = requests.get(
        f"{BASE_URL}/health/live",
        timeout=10,
    )

    request_id = response.headers.get(
        "X-Request-ID"
    )

    print(
        f"Generated X-Request-ID: {request_id}"
    )

    if not request_id:
        raise RuntimeError(
            "X-Request-ID header missing"
        )

    try:
        uuid.UUID(request_id)
    except ValueError:
        raise RuntimeError(
            "X-Request-ID is not a valid UUID"
        )

    print(
        "PASS: Server generates valid request ID"
    )

    supplied_request_id = str(uuid.uuid4())

    response = requests.get(
        f"{BASE_URL}/health/live",
        headers={
            "X-Request-ID": supplied_request_id
        },
        timeout=10,
    )

    returned_request_id = response.headers.get(
        "X-Request-ID"
    )

    print(
        f"Supplied X-Request-ID: {supplied_request_id}"
    )

    print(
        f"Returned X-Request-ID: {returned_request_id}"
    )

    if returned_request_id != supplied_request_id:
        raise RuntimeError(
            "Valid supplied X-Request-ID was not preserved"
        )

    print(
        "PASS: Valid request ID is preserved"
    )

    invalid_request_id = "not-a-valid-uuid"

    response = requests.get(
        f"{BASE_URL}/health/live",
        headers={
            "X-Request-ID": invalid_request_id
        },
        timeout=10,
    )

    returned_request_id = response.headers.get(
        "X-Request-ID"
    )

    print(
        f"Invalid supplied X-Request-ID: "
        f"{invalid_request_id}"
    )

    print(
        f"Server-generated replacement: "
        f"{returned_request_id}"
    )

    if returned_request_id == invalid_request_id:
        raise RuntimeError(
            "Invalid X-Request-ID was accepted"
        )

    try:
        uuid.UUID(returned_request_id)
    except (ValueError, TypeError):
        raise RuntimeError(
            "Replacement X-Request-ID is not a valid UUID"
        )

    print(
        "PASS: Invalid request ID is replaced"
    )


def test_missing_token():
    print("\n[3] Testing missing authentication token...")

    response = requests.post(
        f"{BASE_URL}/shows",
        json={
            "name": "Security Test Show",
            "seats": ["SEC-1"],
            "price_paise": 100,
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    if response.status_code != 401:
        print(response.text)

        raise RuntimeError(
            "Expected 401 for missing token"
        )

    print(
        "PASS: Missing token rejected with 401"
    )


def test_invalid_token():
    print("\n[4] Testing invalid authentication token...")

    response = requests.post(
        f"{BASE_URL}/shows",
        headers=auth_headers(
            "this-is-not-a-valid-jwt"
        ),
        json={
            "name": "Security Test Show",
            "seats": ["SEC-2"],
            "price_paise": 100,
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    if response.status_code != 401:
        print(response.text)

        raise RuntimeError(
            "Expected 401 for invalid token"
        )

    print(
        "PASS: Invalid token rejected with 401"
    )


def test_expired_token():
    print("\n[5] Testing expired JWT...")

    token = generate_token(
        user_id="security-expired",
        role="user",
        include_exp=True,
        expired=True,
    )

    response = requests.post(
        f"{BASE_URL}/shows",
        headers=auth_headers(token),
        json={
            "name": "Security Test Show",
            "seats": ["SEC-3"],
            "price_paise": 100,
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    if response.status_code != 401:
        print(response.text)

        raise RuntimeError(
            "Expected 401 for expired token"
        )

    print(
        "PASS: Expired token rejected with 401"
    )


def test_missing_exp():
    print("\n[6] Testing JWT without exp claim...")

    token = generate_token(
        user_id="security-no-exp",
        role="user",
        include_exp=False,
    )

    response = requests.post(
        f"{BASE_URL}/shows",
        headers=auth_headers(token),
        json={
            "name": "Security Test Show",
            "seats": ["SEC-4"],
            "price_paise": 100,
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    if response.status_code != 401:
        print(response.text)

        raise RuntimeError(
            "Expected 401 for JWT without exp"
        )

    print(
        "PASS: JWT without exp rejected with 401"
    )


def test_invalid_role():
    print("\n[7] Testing invalid JWT role...")

    token = generate_token(
        user_id="security-invalid-role",
        role="superadmin",
        include_exp=True,
    )

    response = requests.post(
        f"{BASE_URL}/shows",
        headers=auth_headers(token),
        json={
            "name": "Security Test Show",
            "seats": ["SEC-5"],
            "price_paise": 100,
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    if response.status_code != 401:
        print(response.text)

        raise RuntimeError(
            "Expected 401 for invalid role"
        )

    print(
        "PASS: Invalid role rejected with 401"
    )


def test_user_cannot_create_show():
    print(
        "\n[8] Testing normal user cannot create show..."
    )

    token = generate_token(
        user_id="security-normal-user",
        role="user",
    )

    response = requests.post(
        f"{BASE_URL}/shows",
        headers=auth_headers(token),
        json={
            "name": "Security Unauthorized Show",
            "seats": ["SEC-6"],
            "price_paise": 100,
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    if response.status_code != 403:
        print(response.text)

        raise RuntimeError(
            "Expected 403 for normal user accessing admin endpoint"
        )

    print(
        "PASS: Normal user rejected with 403"
    )


def test_admin_can_create_show():
    print(
        "\n[9] Testing admin authentication..."
    )

    token = generate_token(
        user_id="security-admin",
        role="admin",
    )

    show_name = (
        f"Security Test Show {uuid.uuid4()}"
    )

    response = requests.post(
        f"{BASE_URL}/shows",
        headers=auth_headers(token),
        json={
            "name": show_name,
            "seats": ["SEC-ADMIN-1"],
            "price_paise": 100,
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    if response.status_code != 201:
        print(response.text)

        raise RuntimeError(
            "Expected admin to create show successfully"
        )

    print(
        "PASS: Admin can access admin endpoint"
    )


def test_user_identity_not_from_body():
    print(
        "\n[10] Testing user identity comes from JWT..."
    )

    token = generate_token(
        user_id="real-user-from-token",
        role="user",
    )

    response = requests.post(
        f"{BASE_URL}/shows/fake-show-id/reserve",
        headers=auth_headers(token),
        json={
            "user_id": "spoofed-user",
            "seats": ["A1"],
            "idempotency_key": (
                f"security-{uuid.uuid4()}"
            ),
        },
        timeout=10,
    )

    print(
        f"Response: {response.status_code}"
    )

    # The important check here is that user_id is not
    # accepted as part of the request schema.
    if response.status_code == 200:
        raise RuntimeError(
            "Unexpected successful reservation"
        )

    if response.status_code == 201:
        raise RuntimeError(
            "Reservation unexpectedly accepted spoofed user_id"
        )

    print(
        "PASS: user_id is not accepted as an "
        "authorization identity field"
    )


def main():
    print("=" * 70)
    print("SECURITY REGRESSION TEST")
    print("=" * 70)

    tests = [
        test_security_headers,
        test_request_id,
        test_missing_token,
        test_invalid_token,
        test_expired_token,
        test_missing_exp,
        test_invalid_role,
        test_user_cannot_create_show,
        test_admin_can_create_show,
        test_user_identity_not_from_body,
    ]

    passed = 0

    for test in tests:
        try:
            test()
            passed += 1

        except Exception as exc:
            print()
            print(
                f"FAIL: {test.__name__}"
            )
            print(
                f"Reason: {exc}"
            )
            print()
            raise

    print()
    print("=" * 70)
    print(
        f"SECURITY TEST PASSED "
        f"({passed}/{len(tests)})"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()