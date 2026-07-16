-- ========================================
-- VA PORTAL DATABASE MIGRATION
-- Full service request system
-- ========================================

-- 1. VA Users Table
CREATE TABLE IF NOT EXISTS va_users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    phone TEXT,
    active BOOLEAN DEFAULT TRUE,
    
    -- Performance tracking
    total_completed INTEGER DEFAULT 0,
    total_earned DECIMAL(10,2) DEFAULT 0,
    average_rating DECIMAL(3,2),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);

-- 2. Service Requests Table
CREATE TABLE IF NOT EXISTS service_requests (
    id SERIAL PRIMARY KEY,
    
    -- Service details
    service_type TEXT NOT NULL, -- 'skiptrace', 'cold_call_block', 'postcard', 'door_knock'
    
    -- Property reference
    property_id INTEGER REFERENCES properties(id),
    parcel TEXT NOT NULL,
    property_address TEXT,
    owner_name TEXT,
    
    -- User who requested
    user_email TEXT NOT NULL,
    
    -- Status workflow: pending -> claimed -> in_progress -> submitted -> completed/rejected
    status TEXT DEFAULT 'pending',
    
    -- VA assignment
    claimed_by_va_id INTEGER REFERENCES va_users(id),
    claimed_by_va_email TEXT,
    claimed_at TIMESTAMP,
    started_at TIMESTAMP,
    submitted_at TIMESTAMP,
    completed_at TIMESTAMP,
    rejected_at TIMESTAMP,
    rejection_reason TEXT,
    
    -- For cold calling - time tracking
    hours_purchased DECIMAL(5,2), -- e.g., 1.0, 5.0, 10.0
    hours_used DECIMAL(5,2) DEFAULT 0,
    
    -- Results from VA
    phone TEXT,
    email TEXT,
    notes TEXT,
    call_outcome TEXT, -- For cold calls: 'answered', 'voicemail', 'no_answer', 'wrong_number', 'disconnected'
    
    -- Payment
    amount_charged DECIMAL(10,2) NOT NULL,
    va_payout DECIMAL(10,2) NOT NULL,
    va_paid BOOLEAN DEFAULT FALSE,
    va_paid_at TIMESTAMP,
    stripe_payment_id TEXT,
    
    -- Admin approval
    reviewed_by_admin TEXT,
    reviewed_at TIMESTAMP,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 3. Service Request Properties (for cold calling multiple properties)
CREATE TABLE IF NOT EXISTS service_request_properties (
    id SERIAL PRIMARY KEY,
    service_request_id INTEGER REFERENCES service_requests(id) ON DELETE CASCADE,
    property_id INTEGER REFERENCES properties(id),
    parcel TEXT NOT NULL,
    
    -- Call tracking
    call_attempted BOOLEAN DEFAULT FALSE,
    call_outcome TEXT,
    call_notes TEXT,
    called_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. VA Activity Log
CREATE TABLE IF NOT EXISTS va_activity_log (
    id SERIAL PRIMARY KEY,
    va_id INTEGER REFERENCES va_users(id),
    service_request_id INTEGER REFERENCES service_requests(id),
    action TEXT NOT NULL, -- 'claimed', 'started', 'submitted', 'cancelled'
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 5. VA Payments Table
CREATE TABLE IF NOT EXISTS va_payments (
    id SERIAL PRIMARY KEY,
    va_id INTEGER REFERENCES va_users(id),
    service_request_id INTEGER REFERENCES service_requests(id),
    amount DECIMAL(10,2) NOT NULL,
    payment_method TEXT, -- 'stripe', 'paypal', 'manual'
    payment_reference TEXT,
    paid_at TIMESTAMP DEFAULT NOW(),
    notes TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_service_requests_status ON service_requests(status);
CREATE INDEX IF NOT EXISTS idx_service_requests_va ON service_requests(claimed_by_va_id);
CREATE INDEX IF NOT EXISTS idx_service_requests_user ON service_requests(user_email);
CREATE INDEX IF NOT EXISTS idx_service_requests_created ON service_requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_va_users_email ON va_users(email);
CREATE INDEX IF NOT EXISTS idx_va_users_active ON va_users(active);

-- Sample VA user (password: va123456)
-- api_va_login() checks this with werkzeug's check_password_hash(), which
-- does NOT understand bcrypt's $2b$ format -- the original seed hash here
-- was bcrypt, so this account could never actually log in. Regenerate with:
-- from werkzeug.security import generate_password_hash; generate_password_hash('va123456')
INSERT INTO va_users (email, name, password_hash, phone)
VALUES (
    'va@example.com',
    'Sample VA',
    'scrypt:32768:8:1$fC4mZ7zaS3D8uKcc$0316c39c38225db2368df6cc3dc3b8dd7ac3233d72f10afb76db9d18df0e851cad3c68877e3adf71037e898bccb5cbd93d5a6e162e47bc9ccfe6548bc77fac57',
    '555-0100'
) ON CONFLICT (email) DO NOTHING;

-- Fix the hash on this row if it was already seeded with the old broken
-- bcrypt hash (ON CONFLICT DO NOTHING above won't touch an existing row).
UPDATE va_users SET password_hash = 'scrypt:32768:8:1$fC4mZ7zaS3D8uKcc$0316c39c38225db2368df6cc3dc3b8dd7ac3233d72f10afb76db9d18df0e851cad3c68877e3adf71037e898bccb5cbd93d5a6e162e47bc9ccfe6548bc77fac57'
WHERE email = 'va@example.com' AND password_hash = '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5ew2hBLLPknje';

-- Grant necessary permissions (adjust based on your setup)
-- ALTER TABLE va_users OWNER TO your_db_user;
-- ALTER TABLE service_requests OWNER TO your_db_user;
-- ALTER TABLE service_request_properties OWNER TO your_db_user;
-- ALTER TABLE va_activity_log OWNER TO your_db_user;
-- ALTER TABLE va_payments OWNER TO your_db_user;
