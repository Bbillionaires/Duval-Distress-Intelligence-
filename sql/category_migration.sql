-- api_properties() and the bulk-upload insert path both read/write a
-- properties.category column that was never added to the schema, so
-- GET /api/properties (and bulk upload) 500 with UndefinedColumn on
-- every call. Default matches the app's own fallback ("tax") so
-- existing tax-lien rows keep showing up under the default filter.
ALTER TABLE properties ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'tax';
CREATE INDEX IF NOT EXISTS idx_properties_category ON properties(category);
