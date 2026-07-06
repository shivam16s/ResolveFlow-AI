PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  plan_name TEXT NOT NULL,
  monthly_price REAL NOT NULL CHECK (monthly_price >= 0),
  speed_mbps INTEGER NOT NULL CHECK (speed_mbps > 0),
  benefits TEXT NOT NULL DEFAULT '[]',
  cancellation_fee REAL NOT NULL DEFAULT 0 CHECK (cancellation_fee >= 0)
);

CREATE TABLE IF NOT EXISTS customers (
  customer_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  location TEXT NOT NULL,
  plan_id TEXT NOT NULL REFERENCES plans(plan_id),
  pending_plan_id TEXT REFERENCES plans(plan_id),
  pending_plan_effective_date DATE,
  pending_plan_requested_at DATETIME,
  risk_level TEXT NOT NULL DEFAULT 'low'
    CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
  preferred_language TEXT NOT NULL DEFAULT 'en',
  account_status TEXT NOT NULL DEFAULT 'active'
    CHECK (account_status IN ('active', 'suspended', 'pending_cancellation')),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  churn_score REAL NOT NULL DEFAULT 0 CHECK (churn_score >= 0 AND churn_score <= 1)
);

CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  amount REAL NOT NULL CHECK (amount >= 0),
  date DATETIME NOT NULL,
  method TEXT NOT NULL,
  duplicate_flag INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_flag IN (0, 1))
);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  amount REAL NOT NULL CHECK (amount >= 0),
  date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('paid', 'pending', 'disputed')),
  payment_id TEXT UNIQUE REFERENCES payments(payment_id)
);

CREATE TABLE IF NOT EXISTS outages (
  outage_id TEXT PRIMARY KEY,
  location TEXT NOT NULL,
  start_time DATETIME NOT NULL,
  end_time DATETIME,
  duration_hours REAL CHECK (duration_hours IS NULL OR duration_hours >= 0),
  verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
  affected_customers TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tickets (
  ticket_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  issue_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'in_progress', 'resolved', 'escalated')),
  priority TEXT NOT NULL DEFAULT 'medium'
    CHECK (priority IN ('low', 'medium', 'high', 'critical')),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  appointment_id TEXT UNIQUE,
  appointment_slot TEXT,
  technician_name TEXT,
  scheduled_at DATETIME,
  resolved_at DATETIME
);

CREATE TABLE IF NOT EXISTS policies (
  policy_id TEXT PRIMARY KEY,
  policy_name TEXT NOT NULL UNIQUE,
  policy_text TEXT NOT NULL,
  effective_date DATE NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
);

CREATE TABLE IF NOT EXISTS diagnostics (
  customer_id TEXT PRIMARY KEY REFERENCES customers(customer_id),
  router_status TEXT NOT NULL
    CHECK (router_status IN ('ok', 'degraded', 'offline')),
  signal_strength INTEGER NOT NULL CHECK (signal_strength >= 0 AND signal_strength <= 100),
  last_checked DATETIME NOT NULL,
  recommendation TEXT
);

CREATE TABLE IF NOT EXISTS credits (
  credit_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  amount REAL NOT NULL CHECK (amount > 0),
  reason TEXT NOT NULL,
  policy_id TEXT REFERENCES policies(policy_id),
  applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  applied_to_invoice TEXT REFERENCES invoices(invoice_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
  case_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  session_id TEXT NOT NULL,
  tools_called TEXT NOT NULL DEFAULT '[]',
  evidence_used TEXT NOT NULL DEFAULT '[]',
  action_taken TEXT NOT NULL DEFAULT '[]',
  policy_dag_path TEXT NOT NULL DEFAULT '[]',
  ujcs REAL CHECK (ujcs IS NULL OR (ujcs >= 0 AND ujcs <= 1)),
  policy_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (policy_status IN ('pending', 'compliant', 'non_compliant', 'needs_review')),
  health_score REAL CHECK (health_score IS NULL OR (health_score >= 0 AND health_score <= 100)),
  handoff_required INTEGER NOT NULL DEFAULT 0 CHECK (handoff_required IN (0, 1)),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS human_handoff_queue (
  handoff_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES audit_logs(case_id),
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  context_card TEXT NOT NULL DEFAULT '{}',
  handoff_reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'waiting'
    CHECK (status IN ('waiting', 'assigned', 'resolved')),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  assigned_to TEXT
);

CREATE TABLE IF NOT EXISTS memory_store (
  memory_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  memory_type TEXT NOT NULL
    CHECK (memory_type IN ('stable', 'episodic', 'session')),
  content TEXT NOT NULL,
  entity_tags TEXT NOT NULL DEFAULT '[]',
  session_id TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
  session_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  messages TEXT NOT NULL DEFAULT '[]',
  intents TEXT NOT NULL DEFAULT '[]',
  slots TEXT NOT NULL DEFAULT '{}',
  tools_called TEXT NOT NULL DEFAULT '[]',
  health_scores TEXT NOT NULL DEFAULT '[]',
  final_status TEXT NOT NULL DEFAULT 'active'
    CHECK (final_status IN ('active', 'resolved', 'escalated', 'abandoned')),
  relationship_score_start REAL CHECK (
    relationship_score_start IS NULL OR
    (relationship_score_start >= 0 AND relationship_score_start <= 100)
  ),
  relationship_score_end REAL CHECK (
    relationship_score_end IS NULL OR
    (relationship_score_end >= 0 AND relationship_score_end <= 100)
  ),
  relationship_delta REAL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME
);

CREATE TABLE IF NOT EXISTS telemetry (
  telemetry_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  turn_count INTEGER NOT NULL CHECK (turn_count >= 0),
  latency_ms REAL NOT NULL CHECK (latency_ms >= 0),
  input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
  output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
  total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
  stage_breakdown TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_customers_plan_id ON customers(plan_id);
CREATE INDEX IF NOT EXISTS idx_customers_location ON customers(location);
CREATE INDEX IF NOT EXISTS idx_invoices_customer_id ON invoices(customer_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_payment_id_unique
ON invoices(payment_id)
WHERE payment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payments_customer_id_date ON payments(customer_id, date);
CREATE INDEX IF NOT EXISTS idx_outages_location_verified ON outages(location, verified);
CREATE INDEX IF NOT EXISTS idx_tickets_customer_id_status ON tickets(customer_id, status);
CREATE INDEX IF NOT EXISTS idx_policies_policy_name ON policies(policy_name);
CREATE INDEX IF NOT EXISTS idx_credits_customer_id ON credits(customer_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_customer_id ON audit_logs(customer_id);
CREATE INDEX IF NOT EXISTS idx_handoff_status ON human_handoff_queue(status);
CREATE INDEX IF NOT EXISTS idx_memory_store_customer_type ON memory_store(customer_id, memory_type);
CREATE INDEX IF NOT EXISTS idx_conversations_customer_id ON conversations(customer_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_session_id ON telemetry(session_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_created_at ON telemetry(created_at);
