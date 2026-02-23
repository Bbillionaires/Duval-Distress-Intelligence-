-- File Uploads for Service Requests
CREATE TABLE IF NOT EXISTS service_request_files (
    id SERIAL PRIMARY KEY,
    service_request_id INTEGER REFERENCES service_requests(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    file_size BIGINT NOT NULL, -- in bytes
    file_type TEXT, -- mime type
    file_url TEXT NOT NULL, -- URL to file in storage
    uploaded_by TEXT, -- VA email
    uploaded_at TIMESTAMP DEFAULT NOW()
);

-- Screen Monitoring Screenshots
CREATE TABLE IF NOT EXISTS va_screenshots (
    id SERIAL PRIMARY KEY,
    service_request_id INTEGER REFERENCES service_requests(id) ON DELETE CASCADE,
    va_id INTEGER REFERENCES va_users(id),
    screenshot_url TEXT NOT NULL,
    screenshot_size BIGINT,
    captured_at TIMESTAMP DEFAULT NOW(),
    activity_level TEXT -- 'active', 'idle', 'away'
);

-- Timer Tracking
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS timer_started_at TIMESTAMP;
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS timer_paused_at TIMESTAMP;
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS total_time_seconds INTEGER DEFAULT 0;
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_service_request_files_request ON service_request_files(service_request_id);
CREATE INDEX IF NOT EXISTS idx_screenshots_request ON va_screenshots(service_request_id);
CREATE INDEX IF NOT EXISTS idx_screenshots_va ON va_screenshots(va_id);

-- Add payment fields to service_requests
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT;
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS payment_status TEXT DEFAULT 'pending'; -- pending, paid, failed, refunded
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP;
