-- Property notes table
CREATE TABLE IF NOT EXISTS property_notes (
    id SERIAL PRIMARY KEY,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    parcel TEXT NOT NULL,
    user_email TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_property_notes_property ON property_notes(property_id);
CREATE INDEX IF NOT EXISTS idx_property_notes_parcel ON property_notes(parcel);
CREATE INDEX IF NOT EXISTS idx_property_notes_created ON property_notes(created_at DESC);
