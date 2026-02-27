-- Files already exist in week1_features_migration.sql
-- But let's verify the table structure

-- Service request files table (should already exist)
CREATE TABLE IF NOT EXISTS service_request_files (
    id SERIAL PRIMARY KEY,
    service_request_id INTEGER NOT NULL REFERENCES service_requests(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    file_type TEXT NOT NULL,
    file_url TEXT NOT NULL,
    uploaded_by_va_email TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_service_request_files_request ON service_request_files(service_request_id);
CREATE INDEX IF NOT EXISTS idx_service_request_files_uploaded ON service_request_files(uploaded_at DESC);

-- Add file upload limits as comments for reference
COMMENT ON TABLE service_request_files IS 'Max 50MB per file, 200MB total per service request';
