-- VA portal session table -- mirrors the real `sessions` table but scoped
-- to va_users. Every VA login/session-check query 500s without this.
CREATE TABLE IF NOT EXISTS va_sessions (
    token TEXT PRIMARY KEY,
    va_user_id INTEGER NOT NULL REFERENCES va_users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_va_sessions_va_user ON va_sessions(va_user_id);
CREATE INDEX IF NOT EXISTS idx_va_sessions_expires ON va_sessions(expires_at);

-- Forgot/reset-password flow -- always 500'd with no table to store tokens in.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    user_type TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_token ON password_reset_tokens(token);
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_email ON password_reset_tokens(email);

-- Management portal staff accounts, distinct identity space from `users`.
CREATE TABLE IF NOT EXISTS management_users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_by TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_management_users_email ON management_users(email);
CREATE INDEX IF NOT EXISTS idx_management_users_active ON management_users(active);

-- Management portal sessions. Previously the code inserted into the real
-- `sessions` table with columns (user_email, created_at) that table doesn't
-- have, and joined management_users to sessions by email -- always failed.
-- Give it its own session table instead of weakening the real sessions'
-- NOT NULL FK to users(id) just to shoehorn in a second identity space.
CREATE TABLE IF NOT EXISTS management_sessions (
    token TEXT PRIMARY KEY,
    management_user_id INTEGER NOT NULL REFERENCES management_users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_management_sessions_user ON management_sessions(management_user_id);

-- Management "flagged/disputed submission" queue reads/writes these columns
-- on service_requests; they were never added by any migration.
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS scan_status TEXT;
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS scan_flagged_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_service_requests_scan_status ON service_requests(scan_status);

-- Platform-wide activity log written by log_activity() (target_id is always
-- str()'d before insert, so TEXT not INTEGER) and read by the management
-- activity-log page.
CREATE TABLE IF NOT EXISTS platform_activity_log (
    id SERIAL PRIMARY KEY,
    actor_email TEXT,
    actor_role TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details TEXT,
    ip_address TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_platform_activity_log_created ON platform_activity_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_platform_activity_log_actor ON platform_activity_log(actor_email);

-- service_request_files: the upload/list code already uses the correct
-- column names (file_name = generated unique name, original_filename =
-- human-readable name, mime_type), the table just never grew to match.
ALTER TABLE service_request_files ADD COLUMN IF NOT EXISTS original_filename TEXT;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'service_request_files' AND column_name = 'file_type')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'service_request_files' AND column_name = 'mime_type')
    THEN
        ALTER TABLE service_request_files RENAME COLUMN file_type TO mime_type;
    END IF;
END $$;
