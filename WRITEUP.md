# Seat Reservation at Scale — Technical Write-up

## 1. Problem

The system provides a seat reservation API where many users may attempt to reserve the same seat simultaneously.

The primary correctness requirement is:

> A seat must never be sold to more than one user.

The system must additionally handle:

* concurrent requests
* duplicate client retries
* per-user reservation limits
* multi-seat reservations
* cancellation
* request rate limiting
* observability
* authentication and authorization
* operational health checks

The most important design decision was therefore to make PostgreSQL the source of truth for seat ownership.

---

# 2. High-Level Architecture

```text
Client
  │
  │ HTTP / JSON
  ▼
FastAPI
  │
  ├── Authentication / Authorization
  ├── Request validation
  ├── Rate limiting
  ├── Request ID
  └── Structured logging
  │
  ▼
Reservation Service
  │
  └── PostgreSQL transaction
        │
        ├── user_show_limits
        ├── idempotency_keys
        ├── seats
        ├── reservations
        └── reservation_seats
```

Prometheus consumes the application's `/metrics` endpoint.

---

# 3. Why PostgreSQL Is the Source of Truth

A reservation system requires strong consistency around seat ownership.

An in-memory structure such as:

```python
locked_seats = set()
```

would not be sufficient because:

* multiple application instances would have separate memory
* process restarts would lose state
* locks would not survive failures
* application state could diverge from the database

PostgreSQL provides:

* transactional updates
* row-level locking
* unique constraints
* atomic commits
* durable state

Therefore, the database remains authoritative.

---

# 4. Reservation Transaction

A reservation follows a single database transaction.

Conceptually:

```text
BEGIN

1. Validate show
2. Ensure user_show_limits row exists
3. Lock user_show_limits row
4. Ensure idempotency row exists
5. Lock idempotency row
6. Validate idempotency request hash
7. Check existing reservation
8. Check per-user limit
9. Lock requested seats
10. Verify every seat is available
11. Create reservation
12. Create reservation_seats records
13. Mark seats confirmed
14. Increment active seat count
15. Store reservation ID against idempotency key

COMMIT
```

If any step fails, the transaction is rolled back.

This provides atomicity between:

```text
reservation
seat ownership
user reservation count
idempotency state
```

---

# 5. Lock Ordering

One of the most important concurrency decisions is deterministic lock ordering.

The implementation uses:

```text
user_show_limits
        ↓
idempotency_keys
        ↓
seats
```

Requested seats are normalized and sorted before locking.

For example:

```text
Input:
[A003, A001, A002]

Normalized:
[A001, A002, A003]
```

This means two concurrent multi-seat requests attempt to acquire seat locks in the same order.

Without deterministic ordering, transactions could acquire overlapping locks in opposite orders and increase the possibility of deadlocks.

---

# 6. Hot-seat Concurrency

Consider 500 users attempting:

```text
A001
```

simultaneously.

The first transaction that successfully acquires the seat lock sees:

```text
status = available
```

and changes it to:

```text
status = confirmed
```

The transaction commits.

Subsequent transactions acquire the same row lock after the first transaction completes.

They then observe:

```text
status = confirmed
```

and return:

```text
409 Conflict
```

Therefore:

```text
500 attempts
      │
      ├── 1 × 201 Created
      │
      └── 499 × 409 Conflict
```

No application-level race is required to decide ownership.

The database serialization point is the seat row.

---

# 7. Concurrency Test Result

The final burst test was executed with:

```text
500 users
50 HTTP requests in flight
20 seats
```

The hot-seat target was a single seat.

The result was:

```text
Total requests     : 500
HTTP 201           : 1
HTTP 409           : 499
HTTP 599           : 0
5xx errors         : 0
```

This demonstrates the expected no-double-sell behavior under concurrent contention.

---

# 8. Seat Inventory Invariant

The system maintains three seat states:

```text
available
held
confirmed
```

The inventory invariant is:

```text
available + held + confirmed = total
```

After the burst test:

```text
Total     : 20
Available : 17
Held      : 0
Confirmed : 3
```

Therefore:

```text
17 + 0 + 3 = 20
```

The three confirmed seats correspond to the successful hot-seat reservation and the subsequent two-seat reservation.

---

# 9. Multi-seat Atomicity

Multi-seat reservations use the same database transaction.

Suppose a request contains:

```text
[A001, A002, A003]
```

and:

```text
A001 = available
A002 = confirmed
A003 = available
```

The transaction detects that one requested seat is unavailable.

No seats are confirmed.

The complete request fails with:

```text
409 Conflict
```

This prevents partial reservations.

The behavior is therefore:

```text
all requested seats available
        │
        └── reserve all

any requested seat unavailable
        │
        └── reserve none
```

---

# 10. Idempotency Design

Network clients commonly retry requests when they don't receive a response.

Without idempotency:

```text
Client
  │
  ├── POST reservation
  │       └── reservation succeeds
  │
  └── timeout
       │
       └── retry POST
               └── duplicate reservation
```

The system prevents this using an idempotency key.

The unique scope is:

```text
show_id
user_id
idempotency_key
```

The original request body is represented by a deterministic request hash.

On retry:

```text
same key
same request hash
```

returns the original reservation.

If:

```text
same key
different request hash
```

the request is rejected with `409 Conflict`.

This prevents a key from being reused to represent a different operation.

---

# 11. Per-user Limit

Each show has a configurable reservation limit.

Default:

```text
4 seats per user
```

The system maintains:

```text
user_show_limits.active_seat_count
```

The corresponding row is locked during the reservation transaction.

This is important because a simple application-level check would be vulnerable to:

```text
Request A:
count = 3
limit = 4
→ allowed

Request B:
count = 3
limit = 4
→ allowed

Request A:
increment → 4

Request B:
increment → 5
```

The row lock serializes those updates.

The check and increment occur inside the same transaction.

---

# 12. Cancellation

Cancellation uses the same database concurrency principles.

The operation:

```text
BEGIN

lock user_show_limits
lock reservation
verify ownership
verify reservation state
lock reservation seats
release seats
decrement active seat count
mark reservation cancelled

COMMIT
```

This prevents cancellation from racing incorrectly with another reservation.

For example:

```text
User A cancels A001
        │
        ▼
A001 becomes available
        │
        ▼
User B can reserve A001
```

The state transition is persisted atomically.

---

# 13. Rate Limiting

Reservation endpoints use PostgreSQL-backed fixed-window rate limiting.

The current configuration is:

```text
10 requests
10 seconds
per user
```

The counter uses:

```sql
INSERT ...
ON CONFLICT ...
DO UPDATE
```

This makes concurrent increments atomic.

The system returns:

```text
429 Too Many Requests
```

and includes:

```text
Retry-After
```

in the response.

Redis was intentionally not introduced because it is not necessary for the correctness-critical seat reservation path.

---

# 14. Why No Redis Lock?

Redis distributed locks were considered but not used for seat ownership.

The reservation system already requires PostgreSQL to persist:

```text
seat state
reservation
reservation-seat mapping
user limits
idempotency
```

Introducing a second distributed lock authority would increase system complexity.

A design such as:

```text
Redis lock
   +
PostgreSQL transaction
```

would require reasoning about failure cases between the two systems.

Instead:

```text
PostgreSQL transaction
   +
row-level locks
   +
database constraints
```

keeps the correctness boundary in one system.

Redis could still be useful in a larger deployment for non-critical concerns such as distributed caching or high-scale rate limiting, but it is not required for the current reservation correctness model.

---

# 15. Database Constraints

Application-level validation is supplemented with database constraints.

Examples include:

### Unique show seat

```text
(show_id, seat_number)
```

prevents duplicate seat definitions.

### Unique reservation idempotency

```text
(show_id, user_id, idempotency_key)
```

prevents duplicate idempotency records.

### Unique seat reservation

```text
reservation_seats.seat_id
```

prevents a seat from belonging to multiple reservations.

### Monetary validation

```text
price_paise >= 0
amount_paise >= 0
```

These constraints provide defense-in-depth if application code contains an unexpected bug.

---

# 16. Authentication Security

The reservation user is derived from:

```text
JWT.sub
```

rather than the request body.

The API does not accept arbitrary reservation ownership such as:

```json
{
  "user_id": "some-other-user"
}
```

The JWT requires:

```text
sub
role
exp
```

and the token signature is verified before the request reaches the reservation service.

This prevents a client from spoofing another user's identity through request parameters.

---

# 17. Observability

The system exposes Prometheus metrics for:

```text
successful reservations
declined reservations
cancellations
rate-limit rejections
available seats
reservation latency
```

Declines are labelled by reason so operational behavior can be distinguished.

For example:

```text
seat_unavailable
invalid_seat
user_limit
```

Structured JSON logs include:

```text
timestamp
level
logger
request_id
HTTP method
path
status
duration
user ID
show ID
reservation ID
seat count
amount
reason
```

The request ID is also returned to the client through:

```text
X-Request-ID
```

This allows an individual request to be correlated between the client and server logs.

---

# 18. Health Checks

Two health concepts are exposed.

### Liveness

Answers:

> Is the application process running?

It does not depend on PostgreSQL.

### Readiness

Answers:

> Can the application safely serve requests that require PostgreSQL?

The readiness check verifies database connectivity.

If PostgreSQL is unavailable, readiness returns an unsuccessful status rather than claiming that the service is ready.

This distinction is important in container orchestration environments.

---

# 19. Security Headers

The application adds:

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy
```

These reduce unnecessary browser-side attack surface for an API service.

---

# 20. Failure Handling

The reservation operation is designed around transaction rollback.

If an operation fails before commit:

```text
reservation
seat updates
user limit updates
idempotency reservation mapping
```

are rolled back together.

This avoids states such as:

```text
seat = confirmed
reservation = missing
```

or:

```text
reservation = confirmed
seat = available
```

The database transaction is therefore the boundary for reservation state consistency.

---

# 21. Performance Considerations

The hot-seat scenario intentionally creates contention on one database row.

This is an inherent property of the business requirement: only one user can own the seat.

The design therefore prioritizes:

```text
correctness
over
artificially high throughput
```

For non-contended seats, transactions can operate concurrently because PostgreSQL row-level locks target the affected rows.

The connection pool is configured separately from the HTTP client concurrency used by the burst test.

The final burst test uses controlled HTTP concurrency to avoid turning the local Windows HTTP stack into the benchmark bottleneck.

---

# 22. Scalability

The API is designed to be horizontally replicated.

Multiple FastAPI instances can safely serve reservation requests because reservation state is not stored in application memory.

For example:

```text
                 Load Balancer
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       API #1       API #2      API #3
          │           │           │
          └───────────┼───────────┘
                      ▼
                  PostgreSQL
```

Two instances attempting to reserve the same seat still contend on the same PostgreSQL row.

The correctness mechanism therefore survives application-level horizontal scaling.

---

# 23. Operational Scaling Considerations

For a larger production deployment, additional infrastructure would be considered around the same correctness model:

* managed PostgreSQL
* connection pooling
* read replicas for non-critical read workloads
* centralized logs
* Prometheus/Grafana
* distributed tracing
* autoscaling API instances
* database monitoring
* automated backups
* secret management
* HTTPS termination

Read replicas should not be used as the source of truth for seat availability during the reservation transaction because replication lag can expose stale state.

---

# 24. Docker

The repository contains:

```text
Dockerfile
docker-compose.yml
db/init.sql
```

The Docker Compose configuration provisions PostgreSQL and the API.

The Docker setup was designed for reproducible execution.

Due to corporate device restrictions, Docker Desktop/runtime could not be executed on the development machine used for implementation and testing. Local validation was therefore performed directly against PostgreSQL.

---

# 25. Testing Strategy

The project uses several levels of validation.

### Unit-level validation

Validation of:

* request normalization
* seat normalization
* request hashing
* metrics behavior

### API/integration testing

Validation of:

* authentication
* authorization
* reservation lifecycle
* cancellation
* rate limiting
* health endpoints

### Concurrency testing

Validation of:

* hot-seat contention
* concurrent rate limiting
* concurrent cancellation/re-reservation
* per-user limits

### Burst testing

The dedicated 500-user test validates the most important production property:

```text
500 concurrent attempts
        ↓
1 successful reservation
499 conflicts
0 server errors
```

---

# 26. Key Design Trade-offs

## PostgreSQL locking vs distributed locking

PostgreSQL locking was chosen because seat state already belongs in PostgreSQL.

This avoids introducing a second correctness authority.

## Fixed-window rate limiting vs Redis

PostgreSQL was sufficient for the assignment's rate-limiting requirement and avoids another infrastructure dependency.

## All-or-nothing multi-seat reservations

This simplifies correctness and gives clients deterministic behavior.

## Aggregate per-user counter

Maintaining `active_seat_count` avoids repeatedly counting reservations during every reservation request and allows the limit check to be serialized through one row.

## Integer money

Paise are stored as integers instead of floating-point currency values.

---

# 27. Known Limitations

The current implementation intentionally focuses on the assignment's core reservation requirements.

Potential future extensions include:

* temporary seat holds with expiration
* background cleanup of expired holds
* distributed tracing
* richer audit logs
* database migration tooling such as Alembic
* managed secret storage
* production-grade distributed rate limiting
* payment integration
* event-driven reservation notifications
* advanced database partitioning for very large datasets

These extensions are separate from the core no-double-sell mechanism.

---

# 28. AI Assistance

AI tools were used during development for:

* architecture exploration
* concurrency reasoning
* code review
* debugging
* security review
* test-case design
* documentation drafting

The implementation was manually integrated into the project and validated through actual local execution and automated/concurrency tests.

The final design decisions remain based on the application's requirements and observed behavior.

---

# 29. Conclusion

The system uses PostgreSQL as the correctness boundary for seat ownership.

The central reservation transaction combines:

```text
authentication
+
idempotency
+
per-user limit enforcement
+
deterministic row locking
+
database constraints
+
atomic commit
```

The resulting behavior under a 500-user hot-seat storm was:

```text
1 successful reservation
499 conflicts
0 client-side failures
0 server-side 5xx errors
```

with the seat inventory invariant remaining valid:

```text
available + held + confirmed = total
```

This provides a simple and auditable correctness model while allowing the API layer to be horizontally replicated around a shared PostgreSQL source of truth.
