# Seat Reservation at Scale

A production-oriented seat reservation API built with **FastAPI, PostgreSQL, JWT authentication, Prometheus metrics, structured logging, and concurrency-safe database transactions**.

The system is designed around one primary correctness requirement:

> Under heavy concurrent demand for the same seat, exactly one reservation succeeds and the seat can never be double-sold.

The implementation uses PostgreSQL as the source of truth and relies on deterministic row-level locking, database constraints, idempotency keys, and transactional updates rather than an in-memory lock or external distributed lock.

---

## 1. Features

* Admin-only show creation
* Configurable seat inventory
* Integer-only monetary values in paise
* JWT authentication
* Role-based authorization
* Token-derived user identity
* Atomic multi-seat reservations
* All-or-nothing multi-seat behavior
* Idempotent reservation requests
* Per-user reservation limits
* Explicit reservation cancellation
* PostgreSQL row-level concurrency control
* PostgreSQL-backed request rate limiting
* Seat availability reconciliation
* Prometheus metrics
* Structured JSON logging
* Request/correlation IDs
* Liveness and readiness health checks
* Security response headers
* Docker and Docker Compose configuration
* Automated concurrency/burst testing

---

## 2. Technology Stack

| Component               | Technology       |
| ----------------------- | ---------------- |
| API                     | FastAPI          |
| Language                | Python 3.12      |
| Database                | PostgreSQL       |
| ORM / DB Access         | SQLAlchemy Async |
| PostgreSQL Driver       | asyncpg          |
| Authentication          | JWT              |
| Metrics                 | Prometheus       |
| HTTP Client / Load Test | httpx            |
| Testing                 | pytest           |
| Containerization        | Docker           |
| Local Orchestration     | Docker Compose   |

Redis is intentionally not required for reservation correctness. PostgreSQL provides the transactional and locking guarantees needed for seat ownership.

---

## 3. Architecture

```text
                         ┌──────────────────────┐
                         │       Client         │
                         └──────────┬───────────┘
                                    │
                                    │ HTTP / JSON
                                    ▼
                         ┌──────────────────────┐
                         │      FastAPI         │
                         │                      │
                         │ Auth / Validation    │
                         │ Rate Limiting        │
                         │ Request Context      │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │ Reservation Service  │
                         │                      │
                         │ Idempotency          │
                         │ User Limit           │
                         │ Deterministic Locks  │
                         │ Atomic Transaction   │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │     PostgreSQL       │
                         │                      │
                         │ Shows                │
                         │ Seats                │
                         │ Reservations         │
                         │ Idempotency Keys     │
                         │ User Limits           │
                         │ Rate Limits          │
                         └──────────────────────┘

                         ┌──────────────────────┐
                         │     Prometheus       │
                         │       /metrics       │
                         └──────────────────────┘
```

PostgreSQL is the source of truth for seat ownership.

---

## 4. Project Structure

```text
seat-reservation/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── metrics.py
│   │   ├── logging.py
│   │   └── request_context.py
│   ├── db/
│   │   ├── database.py
│   │   └── models.py
│   ├── api/
│   │   ├── health.py
│   │   ├── shows.py
│   │   └── reservations.py
│   ├── schemas/
│   │   ├── show.py
│   │   └── reservation.py
│   └── services/
│       ├── show_service.py
│       ├── reservation_service.py
│       ├── metrics_service.py
│       └── rate_limit_service.py
├── db/
│   └── init.sql
├── scripts/
├── tests/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── requirements.txt
├── README.md
└── WRITEUP.md
```

---

# 5. API

## Create a Show

### `POST /shows`

Admin-only endpoint.

### Request

```json
{
  "name": "Avengers: Secret Wars",
  "seats": [
    "A001",
    "A002",
    "A003"
  ],
  "price_paise": 25000,
  "per_user_limit": 4
}
```

### Response

`201 Created`

```json
{
  "id": "show-id",
  "name": "Avengers: Secret Wars",
  "price_paise": 25000,
  "per_user_limit": 4,
  "seats": [
    {
      "seat_number": "A001",
      "status": "available"
    },
    {
      "seat_number": "A002",
      "status": "available"
    },
    {
      "seat_number": "A003",
      "status": "available"
    }
  ]
}
```

Money is represented as integer paise to avoid floating-point precision issues.

---

# 6. Reserve Seats

### `POST /shows/{show_id}/reserve`

Authenticated users can reserve one or more seats.

### Request

```json
{
  "seats": [
    "A001",
    "A002"
  ],
  "idempotency_key": "8e4d6b1e-..."
}
```

The user identity is derived from the JWT token.

A `user_id` supplied in the request body is not accepted.

### Successful Response

`201 Created`

```json
{
  "reservation_id": "reservation-id",
  "show_id": "show-id",
  "user_id": "user-123",
  "seats": [
    {
      "seat_number": "A001"
    },
    {
      "seat_number": "A002"
    }
  ],
  "amount_paise": 50000,
  "status": "confirmed"
}
```

---

# 7. Multi-seat Reservation Semantics

Multi-seat reservations are **all-or-nothing**.

For example:

```text
Request:
[A001, A002, A003]
```

If `A002` is already unavailable, the entire request is rejected.

The system does not partially reserve:

```text
A001 → reserved
A002 → failed
A003 → reserved
```

Instead, the complete transaction is rolled back.

This prevents partially completed bookings and simplifies client-side recovery.

---

# 8. Idempotency

Reservation requests require an `idempotency_key`.

The key is scoped to:

```text
show_id + user_id + idempotency_key
```

### Same request

If the same user retries the same request using the same idempotency key, the original reservation is returned.

```text
First request  → 201
Retry          → original reservation
```

### Different request

If the same key is reused with a different request body:

```text
First:
[A001]

Second:
[A002]
```

the request is rejected with `409 Conflict`.

This prevents accidental duplicate reservations caused by client retries or network failures.

---

# 9. Concurrency Control

The reservation transaction uses PostgreSQL row-level locks.

Locks are acquired in deterministic order:

```text
1. user_show_limits
2. idempotency_keys
3. requested seats
```

Requested seats are normalized and sorted before acquiring locks.

This provides two important properties:

1. Concurrent users attempting the same seat cannot both reserve it.
2. Multi-seat requests acquire locks in a consistent order, reducing deadlock risk.

The database also provides defense-in-depth through unique constraints.

---

# 10. Per-user Reservation Limit

Each show has a configurable per-user seat limit.

Default:

```text
4 seats
```

The system maintains an aggregate counter in:

```text
user_show_limits
```

The corresponding row is locked during reservation.

Therefore, concurrent requests from the same user cannot race past the configured limit.

---

# 11. Cancellation

Reservations can be explicitly cancelled.

Cancellation:

1. Verifies reservation ownership.
2. Locks the relevant user-show row.
3. Locks the reservation.
4. Locks its seats.
5. Releases the seats.
6. Decrements the user's active seat count.
7. Marks the reservation as cancelled.
8. Commits all changes atomically.

The released seats can subsequently be reserved by another user.

---

# 12. Seat State

Each seat has one of three states:

```text
available
held
confirmed
```

The current implementation uses `confirmed` reservations directly; the `held` state is retained in the model for future temporary-hold/expiration workflows.

The core invariant is:

```text
available + held + confirmed = total
```

The burst test verifies this invariant after high-concurrency activity.

---

# 13. Rate Limiting

Reservation requests are rate-limited per user.

The current implementation uses a PostgreSQL-backed fixed-window counter rather than Redis.

Default configuration:

```text
10 reservation requests
per 10-second window
```

Excess requests receive:

```text
429 Too Many Requests
```

with a `Retry-After` header.

The rate-limit counter uses an atomic PostgreSQL upsert, allowing concurrent requests from the same user to be counted safely.

---

# 14. Authentication and Authorization

JWT tokens contain:

```json
{
  "sub": "user-123",
  "role": "user",
  "exp": 1234567890
}
```

Required claims:

* `sub`
* `role`
* `exp`

Supported roles:

```text
user
admin
```

Admin endpoints require the `admin` role.

The reservation endpoint derives identity exclusively from the authenticated token.

This prevents a client from attempting:

```json
{
  "user_id": "another-user"
}
```

to reserve seats on behalf of another user.

---

# 15. Security

Implemented security controls include:

* JWT signature verification
* Required JWT claims
* JWT expiration validation
* Role-based authorization
* Token-derived identity
* Strict request schemas
* Rejection of unexpected request fields
* Integer-only monetary representation
* Security response headers
* Request/correlation IDs
* Database constraints
* Parameterized SQL
* No user-controlled reservation identity

Security headers include:

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy
```

---

# 16. Observability

## Prometheus Metrics

The application exposes:

```text
GET /metrics
```

Key metrics include:

```text
reservations_confirmed_total
reservations_declined_total
reservations_cancelled_total
rate_limit_rejections_total
seats_available
reservation_request_duration_seconds
```

Declined reservations are labelled by reason, including conditions such as:

```text
seat_unavailable
invalid_seat
user_limit
```

The available-seat gauge is reconciled against PostgreSQL.

---

# 17. Structured Logging

Application logs are emitted as JSON.

Example:

```json
{
  "timestamp": "2026-...",
  "level": "INFO",
  "logger": "seat_reservation",
  "message": "http_request",
  "request_id": "....",
  "method": "POST",
  "path": "/shows/.../reserve",
  "status_code": 201,
  "duration_ms": 42.1
}
```

Every request receives an `X-Request-ID`.

A valid incoming request ID is preserved; invalid or missing IDs are replaced with a generated UUID.

---

# 18. Health Checks

The service exposes:

```text
GET /health/live
GET /health/ready
```

### Liveness

Indicates that the application process is running.

### Readiness

Checks PostgreSQL connectivity.

Readiness fails closed when the database dependency is unavailable, preventing traffic from being routed to an instance that cannot perform reservations.

---

# 19. Testing

The project includes tests covering:

* Authentication
* Authorization
* JWT validation
* Security headers
* Request IDs
* Rate limiting
* Concurrent rate limiting
* Idempotency
* Per-user limits
* Reservation cancellation
* Concurrent cancellation/re-reservation
* Metrics
* Multi-seat atomicity
* Hot-seat concurrency

---

# 20. High-Concurrency Burst Test

The project includes a dedicated burst test:

```text
tests/final_burst_test.py
```

It creates a show and sends concurrent reservation requests from multiple unique users against the same seat.

The test verifies:

```text
Exactly 1 successful reservation
Remaining requests receive 409 Conflict
No 5xx responses
No client-side request failures
Seat reconciliation remains valid
```

A 500-user hot-seat run produced:

```text
Total requests     : 500
HTTP 201            : 1
HTTP 409            : 499
HTTP 599            : 0
5xx errors          : 0
```

The final seat state also satisfied:

```text
Available + Held + Confirmed = Total
```

For a 20-seat show after the hot-seat test and a two-seat reservation:

```text
Available : 17
Held      : 0
Confirmed : 3
Total     : 20
```

The burst test therefore verifies both the no-double-sell property and the inventory reconciliation invariant.

---

# 21. Running Locally

## Prerequisites

* Python 3.12+
* PostgreSQL
* pip
* virtual environment

Create a virtual environment:

```bash
python -m venv seat_reservation_venv
```

Activate it on Windows:

```cmd
seat_reservation_venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure `.env`:

```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/seat_reservation
JWT_SECRET=change-this-to-a-long-random-secret
JWT_ALGORITHM=HS256
```

Start the API:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

# 22. Running Tests

Run the automated test suite:

```bash
pytest -q
```

Run the concurrency test:

### Windows CMD

```cmd
set HOT_SEAT_USERS=500
set HTTP_CONCURRENCY=50
python -m tests.final_burst_test
```

The concurrency test intentionally separates the number of users from HTTP connection concurrency so that hundreds of reservation attempts can be generated without overwhelming the local development HTTP stack.

---

# 23. Docker

Docker configuration is included for reproducible deployment:

```text
Dockerfile
docker-compose.yml
.dockerignore
db/init.sql
```

Docker Compose provisions:

* PostgreSQL
* FastAPI application

The reservation system does not require Redis.

Due to corporate device restrictions, Docker Desktop/runtime could not be executed on the development machine used for implementation and testing. The application was therefore validated directly against PostgreSQL using the documented local setup.

---

# 24. Deployment

The application is designed to run as a containerized FastAPI service backed by PostgreSQL.

Production deployment should provide:

* HTTPS termination
* Secret management
* Managed PostgreSQL
* Application health checks
* Readiness-based traffic routing
* Prometheus-compatible metrics collection
* Centralized structured logs
* Database backups
* Restricted database network access

---

# 25. Design Principles

The implementation prioritizes:

### Correctness before throughput

Seat ownership is determined by PostgreSQL transactions rather than application-memory state.

### Database-enforced invariants

Application checks are backed by unique constraints and transactional updates.

### Deterministic locking

Locks are acquired in a consistent order.

### Idempotent retries

Clients can safely retry reservation requests.

### Explicit identity

User identity comes from authentication rather than request data.

### Observable behavior

Reservation outcomes, latency, seat availability, request IDs, and rejection reasons are observable.

---

# 26. AI Assistance Disclosure

AI tools were used during development for engineering assistance, including:

* Architecture discussion
* Concurrency design review
* Code review and debugging
* Test-case design
* Documentation drafting
* Security and observability review

The final implementation was reviewed, executed, tested, and validated against the application's actual behavior.

AI assistance was used as a development aid rather than as a substitute for implementation validation.


# Local Development Setup

## Prerequisites

The following software is required to run the application locally:

| Requirement | Recommended Version | Purpose                        |
| ----------- | ------------------- | ------------------------------ |
| Python      | 3.12+               | Backend application            |
| PostgreSQL  | 14+                 | Persistent data store          |
| Git         | Latest              | Source code management         |
| pip         | Latest              | Python dependency installation |

### Optional

| Tool           | Purpose                               |
| -------------- | ------------------------------------- |
| Docker         | Reproducible containerized deployment |
| Postman / curl | Manual API testing                    |

Redis and Node.js are **not required** for the current backend implementation.

---

## 1. Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd seat-reservation
```

---

## 2. Create a Python Virtual Environment

### Windows

```cmd
python -m venv seat_reservation_venv
seat_reservation_venv\Scripts\activate
```

### macOS / Linux

```bash
python3 -m venv seat_reservation_venv
source seat_reservation_venv/bin/activate
```

---

## 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

Verify the installation:

```bash
python --version
pip --version
```

---

## 4. Create the PostgreSQL Database

Make sure PostgreSQL is running locally.

Create the database:

```sql
CREATE DATABASE seat_reservation;
```

The application expects the following connection format:

```text
postgresql+asyncpg://<username>:<password>@localhost:5432/seat_reservation
```

---

## 5. Initialize the Database Schema

The complete database schema is available in:

```text
db/init.sql
```

Run it against the `seat_reservation` database.

### Using psql

```bash
psql -U postgres -d seat_reservation -f db/init.sql
```

If PostgreSQL is installed on Windows and `psql` is not available in PATH, the SQL file can also be executed using pgAdmin's Query Tool.

The initialization script creates:

```text
shows
seats
reservations
reservation_seats
idempotency_keys
user_show_limits
request_rate_limits
```

as well as the required PostgreSQL enum types.

---

## 6. Configure Environment Variables

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/seat_reservation
JWT_SECRET=change-this-to-a-long-random-secret
JWT_ALGORITHM=HS256
```

### Environment Variables

| Variable          | Required | Description                         |
| ----------------- | -------- | ----------------------------------- |
| `DATABASE_URL`    | Yes      | Async PostgreSQL connection URL     |
| `JWT_SECRET`      | Yes      | Secret used to sign and verify JWTs |
| `JWT_ALGORITHM`   | No       | JWT algorithm; defaults to `HS256`  |
| `DB_POOL_SIZE`    | No       | Database connection pool size       |
| `DB_MAX_OVERFLOW` | No       | Maximum additional DB connections   |

For local development, the default application configuration uses:

```text
DB pool size      = 10
Max overflow      = 20
```

Do not commit the `.env` file or production secrets to the repository.

---

## 7. Start the Application

From the project root:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For development, `--reload` can be used:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

For concurrency testing, use the non-reload configuration:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API will be available at:

```text
http://127.0.0.1:8000
```

FastAPI's interactive API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

---

## 8. Verify the Application

### Liveness

```bash
curl http://127.0.0.1:8000/health/live
```

Expected:

```json
{
  "status": "ok"
}
```

### Readiness

```bash
curl http://127.0.0.1:8000/health/ready
```

The readiness endpoint verifies PostgreSQL connectivity.

### Metrics

```bash
curl http://127.0.0.1:8000/metrics
```

The endpoint exposes Prometheus-compatible metrics.

---

## 9. Generate a Local JWT

The reservation API requires a JWT.

For local development, a token can be generated using the same secret configured in `.env`.

Example:

```python
import jwt
import time

token = jwt.encode(
    {
        "sub": "user-123",
        "role": "user",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    },
    "change-this-to-a-long-random-secret",
    algorithm="HS256",
)

print(token)
```

For an admin token, use:

```python
"role": "admin"
```

The token identity is taken from the `sub` claim.

The reservation request does not accept a user ID from the request body.

---

## 10. Run Automated Tests

Run the complete test suite:

```bash
pytest -q
```

The tests cover areas including:

* authentication
* authorization
* JWT validation
* security headers
* request IDs
* rate limiting
* concurrent rate limiting
* idempotency
* reservation limits
* cancellation
* concurrent cancellation/re-reservation
* metrics
* multi-seat reservations
* concurrency behavior

---

## 11. Run the 500-User Concurrency Test

The project includes:

```text
tests/final_burst_test.py
```

Start the API without `--reload`:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open another terminal and activate the virtual environment.

### Windows CMD

```cmd
set HOT_SEAT_USERS=500
set HTTP_CONCURRENCY=50
python -m tests.final_burst_test
```

### macOS / Linux

```bash
HOT_SEAT_USERS=500 HTTP_CONCURRENCY=50 python -m tests.final_burst_test
```

The test creates a show and sends 500 reservation attempts for the same seat.

Expected result:

```text
HTTP 201 : 1
HTTP 409 : 499
HTTP 599 : 0
5xx      : 0
```

The test also verifies:

```text
available + held + confirmed = total
```

---

## 12. Docker Setup

Docker configuration is also included:

```text
Dockerfile
docker-compose.yml
db/init.sql
.dockerignore
```

If Docker is available:

```bash
docker compose up --build
```

This provisions:

* PostgreSQL
* FastAPI

The Docker configuration is independent of the local Python/PostgreSQL setup described above.

Due to corporate device restrictions, Docker Desktop/runtime could not be executed on the development machine used for implementation and testing. The application was therefore validated directly against PostgreSQL using the documented local setup.

---

## 13. Troubleshooting

### PostgreSQL connection error

Verify:

```text
PostgreSQL is running
Database exists
Username/password are correct
Port is 5432
DATABASE_URL is correct
```

Example:

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/seat_reservation
```

---

### Port 8000 already in use

Run the application on another port:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Then set:

```cmd
set BASE_URL=http://127.0.0.1:8001
```

before running the burst test.

---

### JWT authentication errors

Make sure the JWT was signed with the same:

```text
JWT_SECRET
JWT_ALGORITHM
```

configured by the application.

The token must contain:

```json
{
  "sub": "user-123",
  "role": "user",
  "exp": 1234567890
}
```

---

### Database schema errors

If the database was created but the tables are missing, rerun:

```bash
psql -U postgres -d seat_reservation -f db/init.sql
```

For a clean local database, recreate the database and initialize it again.
