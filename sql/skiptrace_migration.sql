-- Create skiptracing requests table
CREATE TABLE IF NOT EXISTS skiptrace_requests (
    id SERIAL PRIMARY KEY,
    property_id INTEGER REFERENCES properties(id),
    parcel TEXT NOT NULL,
    user_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, in_progress, completed
    requested_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    va_email TEXT,
    
    -- Contact info found by VA
    phone TEXT,
    email TEXT,
    notes TEXT,
    
    -- Payment
    stripe_payment_id TEXT,
    amount_paid DECIMAL(10,2) DEFAULT 10.00,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_skiptrace_status ON skiptrace_requests(status);
CREATE INDEX IF NOT EXISTS idx_skiptrace_user ON skiptrace_requests(user_email);
CREATE INDEX IF NOT EXISTS idx_skiptrace_parcel ON skiptrace_requests(parcel);

-- Add skiptracing columns to properties table
ALTER TABLE properties ADD COLUMN IF NOT EXISTS skiptrace_status TEXT; -- null, pending, completed
ALTER TABLE properties ADD COLUMN IF NOT EXISTS skiptrace_phone TEXT;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS skiptrace_email TEXT;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS skiptrace_notes TEXT;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS skiptrace_completed_at TIMESTAMP;
