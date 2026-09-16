"""Seed corpus. Stands in for the real document set until the RAG index lands."""

DOCUMENTS: list[dict] = [
    {
        "document_id": "POL-001",
        "document_type": "policy",
        "title": "Payment Security Policy",
        "metadata": {
            "version": "2.1", "owner": "Security Team", "department": "Engineering",
            "status": "active", "effective_date": "2026-01-01", "review_date": "2027-01-01",
        },
        "content": (
            "1. Purpose\nThis policy defines security requirements for processing and storing "
            "payment information.\n2. Scope\nApplies to all services, databases, APIs and "
            "employees involved in payment processing.\n3. Requirements\nPayment credentials must "
            "be encrypted in transit and at rest. Production payment data must not be stored in "
            "development environments. Access follows least privilege.\n4. Exceptions\nExceptions "
            "require Security Team approval with documented business justification."
        ),
    },
    {
        "document_id": "POL-002",
        "document_type": "policy",
        "title": "Data Retention Policy",
        "metadata": {
            "version": "1.4", "owner": "Legal Team", "department": "Compliance",
            "status": "active", "effective_date": "2025-07-01", "review_date": "2026-07-01",
        },
        "content": (
            "Customer records are retained for seven years after account closure. Application "
            "logs are retained 90 days, audit logs 365 days. Deletion requests must be honoured "
            "within 30 days unless a legal hold applies."
        ),
    },
    {
        "document_id": "POL-003",
        "document_type": "policy",
        "title": "Access Control Policy",
        "metadata": {
            "version": "3.0", "owner": "Security Team", "department": "Engineering",
            "status": "under_review", "effective_date": "2026-03-01", "review_date": "2027-03-01",
        },
        "content": (
            "All production access is granted through role based access control. Privileged "
            "access requires multi factor authentication and expires after 8 hours. Quarterly "
            "access reviews are mandatory for every production system."
        ),
    },
    {
        "document_id": "ARC-001",
        "document_type": "architecture",
        "title": "Payment Service Architecture",
        "metadata": {
            "version": "4.2", "owner": "Payments Team", "department": "Engineering",
            "status": "active", "effective_date": "2026-02-15", "review_date": "2027-02-15",
        },
        "content": (
            "The payment service is a stateless Python service behind the API gateway. It writes "
            "to a partitioned PostgreSQL cluster and publishes settlement events to Kafka. Card "
            "data never touches our storage; the service exchanges it for a vault token. Failure "
            "mode: if the vault is unreachable the service fails closed and returns HTTP 503."
        ),
    },
    {
        "document_id": "ARC-002",
        "document_type": "architecture",
        "title": "Identity Platform Architecture",
        "metadata": {
            "version": "2.0", "owner": "Identity Team", "department": "Engineering",
            "status": "active", "effective_date": "2025-11-01", "review_date": "2026-11-01",
        },
        "content": (
            "The identity platform issues short lived OIDC tokens. Sessions are stored in Redis "
            "with a 30 minute sliding expiry. Token signing keys rotate every 24 hours and old "
            "keys stay published for one rotation window to allow in flight validation."
        ),
    },
    {
        "document_id": "ARC-003",
        "document_type": "architecture",
        "title": "Event Streaming Platform",
        "metadata": {
            "version": "1.9", "owner": "Platform Team", "department": "Engineering",
            "status": "active", "effective_date": "2025-09-01", "review_date": "2026-09-01",
        },
        "content": (
            "Kafka runs as three brokers per region with replication factor three. Topics use a "
            "seven day retention default. Consumers must be idempotent; the platform guarantees "
            "at least once delivery only."
        ),
    },
    {
        "document_id": "RUN-001",
        "document_type": "runbook",
        "title": "Payment Gateway Timeout Runbook",
        "metadata": {
            "version": "1.7", "owner": "Payments Team", "department": "Engineering",
            "status": "active", "effective_date": "2026-01-20", "review_date": "2026-07-20",
        },
        "content": (
            "Symptom: elevated 504s on /v1/charges.\nStep 1: check the gateway latency "
            "dashboard.\nStep 2: confirm the upstream acquirer status page.\nStep 3: if acquirer "
            "latency is above 2s, enable the degraded mode flag payments.queue_async.\nStep 4: "
            "page the on call payments engineer if the error rate stays above 5% for 10 minutes."
        ),
    },
    {
        "document_id": "RUN-002",
        "document_type": "runbook",
        "title": "Database Failover Runbook",
        "metadata": {
            "version": "2.3", "owner": "Platform Team", "department": "Engineering",
            "status": "active", "effective_date": "2025-12-01", "review_date": "2026-12-01",
        },
        "content": (
            "Promote the standby with the failover CLI, verify replication lag is zero, update "
            "the connection alias in the service registry, then restart dependent services in "
            "dependency order. Expected total downtime is under four minutes."
        ),
    },
    {
        "document_id": "RUN-003",
        "document_type": "runbook",
        "title": "Token Signing Key Rotation Runbook",
        "metadata": {
            "version": "1.1", "owner": "Identity Team", "department": "Engineering",
            "status": "deprecated", "effective_date": "2025-04-01", "review_date": "2026-04-01",
        },
        "content": (
            "Manual rotation procedure, superseded by automated rotation in identity platform "
            "2.0. Retained for break glass use only."
        ),
    },
    {
        "document_id": "INC-001",
        "document_type": "incident_report",
        "title": "INC-1042 Payment Outage 2026-03-04",
        "metadata": {
            "version": "1.0", "owner": "Payments Team", "department": "Engineering",
            "status": "closed", "severity": "sev1", "effective_date": "2026-03-04",
            "review_date": "2026-04-04",
        },
        "content": (
            "Impact: 42 minutes of failed card authorisations. Root cause: vault client "
            "connection pool exhaustion after a deploy halved the pool size. Detection: alert on "
            "charge success rate. Remediation: rolled back the deploy and raised the pool size. "
            "Action items: add a pool saturation alert, add a load test gate before deploy."
        ),
    },
    {
        "document_id": "INC-002",
        "document_type": "incident_report",
        "title": "INC-1088 Login Failures 2026-05-19",
        "metadata": {
            "version": "1.0", "owner": "Identity Team", "department": "Engineering",
            "status": "closed", "severity": "sev2", "effective_date": "2026-05-19",
            "review_date": "2026-06-19",
        },
        "content": (
            "Impact: 11% of logins failed for 18 minutes. Root cause: a Redis node eviction "
            "dropped active sessions during a maintenance window. Action items: move the "
            "maintenance window outside peak hours, add session store capacity alerts."
        ),
    },
    {
        "document_id": "INC-003",
        "document_type": "incident_report",
        "title": "INC-1120 Settlement Lag 2026-07-02",
        "metadata": {
            "version": "1.0", "owner": "Platform Team", "department": "Engineering",
            "status": "closed", "severity": "sev3", "effective_date": "2026-07-02",
            "review_date": "2026-08-02",
        },
        "content": (
            "Impact: settlement events lagged six hours. Root cause: a slow consumer in the "
            "reconciliation service blocked a partition. Action items: add consumer lag alerting "
            "and partition level dashboards."
        ),
    },
    {
        "document_id": "SPEC-001",
        "document_type": "product_spec",
        "title": "Instant Refunds Product Specification",
        "metadata": {
            "version": "0.9", "owner": "Product Team", "department": "Product",
            "status": "draft", "effective_date": "2026-06-01", "review_date": "2026-12-01",
        },
        "content": (
            "Instant refunds return funds to the original payment method within 30 minutes for "
            "eligible transactions under 500 USD. Out of scope: partial refunds on instalment "
            "plans. Success metric: 80% of eligible refunds settled within the SLA."
        ),
    },
    {
        "document_id": "SPEC-002",
        "document_type": "product_spec",
        "title": "Merchant Self Service Onboarding",
        "metadata": {
            "version": "1.2", "owner": "Product Team", "department": "Product",
            "status": "active", "effective_date": "2026-02-01", "review_date": "2027-02-01",
        },
        "content": (
            "Merchants complete identity verification, bank account linking and risk scoring "
            "without a sales call. Target: median onboarding under 15 minutes with a 65% "
            "completion rate."
        ),
    },
    {
        "document_id": "MTG-001",
        "document_type": "meeting_notes",
        "title": "Payments Architecture Review 2026-04-11",
        "metadata": {
            "version": "1.0", "owner": "Payments Team", "department": "Engineering",
            "status": "active", "effective_date": "2026-04-11", "review_date": "2026-10-11",
        },
        "content": (
            "Agreed to split the settlement worker out of the payment service. Agreed to raise "
            "the vault client pool floor. Open question: whether to adopt regional Kafka clusters "
            "for settlement. Owner: Platform Team, due end of Q3."
        ),
    },
    {
        "document_id": "MTG-002",
        "document_type": "meeting_notes",
        "title": "Security Policy Review 2026-08-22",
        "metadata": {
            "version": "1.0", "owner": "Security Team", "department": "Engineering",
            "status": "active", "effective_date": "2026-08-22", "review_date": "2027-02-22",
        },
        "content": (
            "Reviewed POL-003 revision three. Agreed on 8 hour privileged access expiry. "
            "Deferred the decision on hardware key enforcement to the next quarter."
        ),
    },
]

EMPLOYEES: list[dict] = [
    {"employee_id": "E-1001", "name": "Nadia Perera", "role": "Security Engineer",
     "department": "Engineering", "team": "Security Team", "email": "nadia.perera@example.com",
     "manager": "Ravi Fernando", "location": "Colombo"},
    {"employee_id": "E-1002", "name": "Ravi Fernando", "role": "Engineering Manager",
     "department": "Engineering", "team": "Security Team", "email": "ravi.fernando@example.com",
     "manager": "Sara Wijesinghe", "location": "Colombo"},
    {"employee_id": "E-1003", "name": "Tom Alvarez", "role": "Staff Engineer",
     "department": "Engineering", "team": "Payments Team", "email": "tom.alvarez@example.com",
     "manager": "Sara Wijesinghe", "location": "Madrid"},
    {"employee_id": "E-1004", "name": "Mei Chen", "role": "Site Reliability Engineer",
     "department": "Engineering", "team": "Platform Team", "email": "mei.chen@example.com",
     "manager": "Sara Wijesinghe", "location": "Singapore"},
    {"employee_id": "E-1005", "name": "Sara Wijesinghe", "role": "Director of Engineering",
     "department": "Engineering", "team": "Leadership", "email": "sara.w@example.com",
     "manager": None, "location": "Colombo"},
    {"employee_id": "E-1006", "name": "Jonas Berg", "role": "Product Manager",
     "department": "Product", "team": "Product Team", "email": "jonas.berg@example.com",
     "manager": "Sara Wijesinghe", "location": "Stockholm"},
    {"employee_id": "E-1007", "name": "Ayesha Khan", "role": "Identity Engineer",
     "department": "Engineering", "team": "Identity Team", "email": "ayesha.khan@example.com",
     "manager": "Ravi Fernando", "location": "Dubai"},
]

SERVICES: list[dict] = [
    {"service_id": "SVC-PAY", "name": "payment-service", "tier": "tier-1",
     "owner_team": "Payments Team", "repository": "core/payment-service",
     "oncall": "payments-oncall", "dependencies": ["vault", "postgres-payments", "kafka"],
     "runbook": "RUN-001", "status": "production"},
    {"service_id": "SVC-IDP", "name": "identity-platform", "tier": "tier-1",
     "owner_team": "Identity Team", "repository": "core/identity-platform",
     "oncall": "identity-oncall", "dependencies": ["redis-sessions", "postgres-identity"],
     "runbook": "RUN-003", "status": "production"},
    {"service_id": "SVC-STL", "name": "settlement-worker", "tier": "tier-2",
     "owner_team": "Payments Team", "repository": "core/settlement-worker",
     "oncall": "payments-oncall", "dependencies": ["kafka", "postgres-payments"],
     "runbook": "RUN-002", "status": "production"},
    {"service_id": "SVC-ONB", "name": "merchant-onboarding", "tier": "tier-2",
     "owner_team": "Product Team", "repository": "growth/merchant-onboarding",
     "oncall": "growth-oncall", "dependencies": ["identity-platform", "risk-scoring"],
     "runbook": None, "status": "beta"},
]
