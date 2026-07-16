-- ════════════════════════════════════════════════════════════
-- GROUP A — JV / Direct-Sale Marketplace (dual-model listings)
-- ════════════════════════════════════════════════════════════

-- Admin-created listings that can be JV'd to partners and/or sold direct to end buyers.
CREATE TABLE IF NOT EXISTS property_listings (
    id                          SERIAL PRIMARY KEY,
    property_id                 INTEGER REFERENCES properties(id),
    list_type                   TEXT DEFAULT 'jv_only',      -- jv_only, direct_sale, both
    visibility                  TEXT DEFAULT 'jv_only',
    title                       TEXT,
    description                 TEXT,
    purchase_price              NUMERIC(12,2),
    repair_estimate             NUMERIC(12,2) DEFAULT 0,
    arv                         NUMERIC(12,2),
    total_investment            NUMERIC(12,2) GENERATED ALWAYS AS
                                   (COALESCE(purchase_price,0) + COALESCE(repair_estimate,0)) STORED,
    jv_enabled                  BOOLEAN DEFAULT TRUE,
    jv_split_percentage         NUMERIC(5,2) DEFAULT 40.00,
    jv_terms                    TEXT,
    direct_sale_enabled         BOOLEAN DEFAULT FALSE,
    direct_sale_price           NUMERIC(12,2),
    platform_profit_percentage  NUMERIC(5,2) DEFAULT 80.00,
    earnest_deposit_required    NUMERIC(12,2) DEFAULT 1000.00,
    status                      TEXT DEFAULT 'active',        -- active, closed
    created_by                  TEXT REFERENCES users(email),
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    closed_at                   TIMESTAMPTZ
);

-- A JV partner's claim on a listing, tracking buyer submission through deal closing.
CREATE TABLE IF NOT EXISTS jv_claims (
    id                  SERIAL PRIMARY KEY,
    listing_id          INTEGER REFERENCES property_listings(id) ON DELETE CASCADE,
    user_email          TEXT REFERENCES users(email),
    jv_percentage       NUMERIC(5,2),
    status              TEXT DEFAULT 'active',   -- active, buyer_submitted, buyer_approved, closed
    claimed_at          TIMESTAMPTZ DEFAULT NOW(),
    buyer_name          TEXT,
    buyer_email         TEXT,
    buyer_phone         TEXT,
    buyer_notes         TEXT,
    buyer_submitted_at  TIMESTAMPTZ,
    buyer_approved_at   TIMESTAMPTZ,
    buyer_approved_by   TEXT,
    admin_notes         TEXT,
    final_sale_price    NUMERIC(12,2),
    gross_profit        NUMERIC(12,2),
    partner_earned      NUMERIC(12,2),
    platform_earned     NUMERIC(12,2),
    actual_close_date   DATE,
    payment_status      TEXT
);

-- One-time identity/consent verification a user must complete before JV activity.
CREATE TABLE IF NOT EXISTS jv_verifications (
    id                SERIAL PRIMARY KEY,
    user_email        TEXT UNIQUE REFERENCES users(email),
    full_name         TEXT,
    signature         TEXT,
    id_document_url   TEXT,
    status            TEXT DEFAULT 'pending',   -- pending, approved, rejected
    admin_notes       TEXT,
    submitted_at      TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at       TIMESTAMPTZ
);

-- Partner-submitted deals (subject-to, note, seller-finance, etc.) awaiting admin approval.
CREATE TABLE IF NOT EXISTS jv_partner_listings (
    id                  SERIAL PRIMARY KEY,
    submitted_by        TEXT REFERENCES users(email),
    listing_type        TEXT,   -- standard_jv, non_exclusive, subject_to, seller_finance, note, equity
    address             TEXT,
    asking_split        NUMERIC(5,2),
    purchase_price      NUMERIC(12,2),
    arv                 NUMERIC(12,2),
    description         TEXT,
    loan_balance        NUMERIC(12,2),
    monthly_payment     NUMERIC(12,2),
    note_amount         NUMERIC(12,2),
    equity_percentage   NUMERIC(5,2),
    status              TEXT DEFAULT 'pending_review',  -- pending_review, approved, rejected
    approved_split      NUMERIC(5,2),
    admin_notes         TEXT,
    expires_at          TIMESTAMPTZ,   -- set for non_exclusive listings (NOW() + 30 days)
    submitted_at        TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at         TIMESTAMPTZ
);

-- End buyer's direct-purchase offer/contract on a listing.
CREATE TABLE IF NOT EXISTS direct_purchases (
    id                   SERIAL PRIMARY KEY,
    listing_id           INTEGER REFERENCES property_listings(id) ON DELETE CASCADE,
    buyer_email          TEXT REFERENCES users(email),
    buyer_name           TEXT,
    buyer_phone          TEXT,
    offer_price          NUMERIC(12,2),
    earnest_deposit      NUMERIC(12,2),
    financing_type       TEXT,
    gross_profit         NUMERIC(12,2),
    platform_fee         NUMERIC(12,2),
    platform_percentage  NUMERIC(5,2),
    contract_status      TEXT DEFAULT 'offer_submitted',  -- offer_submitted, ..., cancelled, closed
    offer_submitted_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Analytics log: each time a listing detail page is viewed.
CREATE TABLE IF NOT EXISTS listing_views (
    id           SERIAL PRIMARY KEY,
    listing_id   INTEGER REFERENCES property_listings(id) ON DELETE CASCADE,
    user_email   TEXT REFERENCES users(email),   -- nullable: anonymous viewers allowed
    user_type    TEXT,
    viewed_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_property_listings_status ON property_listings(status);
CREATE INDEX IF NOT EXISTS idx_property_listings_list_type ON property_listings(list_type);
CREATE INDEX IF NOT EXISTS idx_property_listings_property ON property_listings(property_id);
CREATE INDEX IF NOT EXISTS idx_property_listings_created ON property_listings(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jv_claims_listing ON jv_claims(listing_id);
CREATE INDEX IF NOT EXISTS idx_jv_claims_user ON jv_claims(user_email);
CREATE INDEX IF NOT EXISTS idx_jv_claims_status ON jv_claims(status);
CREATE INDEX IF NOT EXISTS idx_jv_partner_listings_submitted_by ON jv_partner_listings(submitted_by);
CREATE INDEX IF NOT EXISTS idx_jv_partner_listings_status ON jv_partner_listings(status);
CREATE INDEX IF NOT EXISTS idx_jv_partner_listings_submitted_at ON jv_partner_listings(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_direct_purchases_listing ON direct_purchases(listing_id);
CREATE INDEX IF NOT EXISTS idx_direct_purchases_buyer ON direct_purchases(buyer_email);
CREATE INDEX IF NOT EXISTS idx_direct_purchases_status ON direct_purchases(contract_status);
CREATE INDEX IF NOT EXISTS idx_listing_views_listing ON listing_views(listing_id);

-- NOT base tables: only ever SELECTed, never INSERTed anywhere in the app. Reconstructed as VIEWs.
CREATE OR REPLACE VIEW active_jv_listings AS
    SELECT * FROM property_listings
    WHERE jv_enabled = TRUE AND status = 'active';

CREATE OR REPLACE VIEW active_direct_listings AS
    SELECT * FROM property_listings
    WHERE direct_sale_enabled = TRUE AND status = 'active';

-- jv_get_earnings() SELECTs one row per user_email from jv_partner_earnings
-- with no INSERT path anywhere -- a per-partner rollup over jv_claims, not
-- a base table. Its own "no data" fallback shape (total_claims=0 etc) is
-- the exact column list to match here.
CREATE OR REPLACE VIEW jv_partner_earnings AS
    SELECT
        user_email,
        COUNT(*) AS total_claims,
        COUNT(*) FILTER (WHERE status IN ('active', 'buyer_submitted', 'buyer_approved')) AS active_claims,
        COUNT(*) FILTER (WHERE status = 'closed') AS closed_deals,
        COALESCE(SUM(partner_earned) FILTER (WHERE status = 'closed' AND payment_status = 'paid'), 0) AS total_paid,
        COALESCE(SUM(partner_earned) FILTER (WHERE status = 'closed' AND (payment_status IS DISTINCT FROM 'paid')), 0) AS pending_payment,
        COALESCE(AVG(partner_earned) FILTER (WHERE status = 'closed'), 0) AS avg_deal_earnings
    FROM jv_claims
    GROUP BY user_email;


-- ════════════════════════════════════════════════════════════
-- GROUP B — CRM (leads / contracts / activity timeline)
-- ════════════════════════════════════════════════════════════

-- A user's CRM lead/pipeline record for a distressed-property owner.
CREATE TABLE IF NOT EXISTS crm_leads (
    id                  SERIAL PRIMARY KEY,
    user_email          TEXT REFERENCES users(email),
    owner_name          TEXT,
    property_address    TEXT,
    phone               TEXT,
    email               TEXT,
    mailing_address     TEXT,
    deal_value          NUMERIC(12,2),
    pipeline_stage      TEXT DEFAULT 'attempted_contact',
    tags                TEXT[] DEFAULT '{}',
    va_assigned         TEXT REFERENCES va_users(email),
    partner_assigned    TEXT REFERENCES users(email),
    notes               TEXT,
    source              TEXT DEFAULT 'manual',   -- manual, csv
    attempts_text       INTEGER DEFAULT 0,
    attempts_email      INTEGER DEFAULT 0,
    attempts_cold_call  INTEGER DEFAULT 0,
    attempts_postcard   INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Uploaded contract document attached to a CRM lead, with expiration tracking.
CREATE TABLE IF NOT EXISTS crm_contracts (
    id               SERIAL PRIMARY KEY,
    lead_id          INTEGER REFERENCES crm_leads(id) ON DELETE CASCADE,
    user_email       TEXT REFERENCES users(email),
    contract_type    TEXT,
    status           TEXT DEFAULT 'not_sent',
    file_url         TEXT,
    expiration_date  DATE,
    notes            TEXT,
    uploaded_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Timeline of notes/stage-changes/contract events for a CRM lead.
CREATE TABLE IF NOT EXISTS crm_activities (
    id             SERIAL PRIMARY KEY,
    lead_id        INTEGER REFERENCES crm_leads(id) ON DELETE CASCADE,
    user_email     TEXT REFERENCES users(email),
    activity_type  TEXT,   -- note, stage_change, contract
    content        TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_leads_user ON crm_leads(user_email);
CREATE INDEX IF NOT EXISTS idx_crm_leads_updated ON crm_leads(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_contracts_lead ON crm_contracts(lead_id);
CREATE INDEX IF NOT EXISTS idx_crm_contracts_expiration ON crm_contracts(expiration_date);
CREATE INDEX IF NOT EXISTS idx_crm_activities_lead ON crm_activities(lead_id);


-- ════════════════════════════════════════════════════════════
-- GROUP C — Buyer Marketplace (listings / offers / messages / favorites)
-- ════════════════════════════════════════════════════════════

-- Buyer-facing marketplace listing (admin-sourced or promoted from an approved JV listing).
CREATE TABLE IF NOT EXISTS marketplace_listings (
    id                  SERIAL PRIMARY KEY,
    source              TEXT DEFAULT 'admin',   -- admin, jv
    listing_type        TEXT,
    address             TEXT,
    price               NUMERIC(12,2),
    arv                 NUMERIC(12,2),
    description         TEXT,
    status              TEXT DEFAULT 'available',  -- available, offer_received, under_contract, sold, off_market
    loan_balance        NUMERIC(12,2),
    monthly_payment     NUMERIC(12,2),
    note_amount         NUMERIC(12,2),
    equity_percentage   NUMERIC(5,2),
    is_performing        BOOLEAN,
    photos              JSONB DEFAULT '[]',
    va_findings         TEXT,
    listed_by           TEXT REFERENCES users(email),
    jv_listing_id       INTEGER REFERENCES jv_partner_listings(id),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- A buyer's purchase offer on a marketplace listing.
CREATE TABLE IF NOT EXISTS marketplace_offers (
    id            SERIAL PRIMARY KEY,
    listing_id    INTEGER REFERENCES marketplace_listings(id) ON DELETE SET NULL,  -- nullable: JV virtual listings have no row here
    buyer_email   TEXT REFERENCES users(email),
    offer_amount  NUMERIC(12,2),
    message       TEXT,
    status        TEXT DEFAULT 'pending',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Contact-info-scrubbed message thread between a buyer and a listing (max 3 buyer messages).
CREATE TABLE IF NOT EXISTS marketplace_messages (
    id               SERIAL PRIMARY KEY,
    listing_id       INTEGER REFERENCES marketplace_listings(id) ON DELETE SET NULL,
    buyer_email      TEXT REFERENCES users(email),
    sender_email     TEXT REFERENCES users(email),
    sender_role      TEXT,   -- buyer, admin
    content          TEXT,
    scan_status      TEXT DEFAULT 'clean',
    response_number  INTEGER,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- A buyer's saved/favorited marketplace listing.
CREATE TABLE IF NOT EXISTS marketplace_favorites (
    id           SERIAL PRIMARY KEY,
    user_email   TEXT REFERENCES users(email),
    listing_id   INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_email, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_listings_status ON marketplace_listings(status);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_created ON marketplace_listings(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_jv ON marketplace_listings(jv_listing_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_offers_listing ON marketplace_offers(listing_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_offers_created ON marketplace_offers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_messages_listing_buyer ON marketplace_messages(listing_id, buyer_email);
CREATE INDEX IF NOT EXISTS idx_marketplace_favorites_user ON marketplace_favorites(user_email);


-- ════════════════════════════════════════════════════════════
-- GROUP D — Admin-only (revenue summary)
-- (platform_activity_log itself is created by sql/auth_tables_migration.sql,
-- since log_activity() is shared by both the management portal and here)
-- ════════════════════════════════════════════════════════════

-- NOT a base table: "SELECT * FROM platform_revenue_summary" has no INSERT path anywhere and no
-- frontend caller either (dead endpoint /api/admin/revenue-summary). Reconstructed as a monthly
-- rollup VIEW of the two known revenue sources (closed JV deals + closed direct-sale purchases).
-- Column shape is a best-effort inference -- no consumer exists to confirm exact fields expected.
CREATE OR REPLACE VIEW platform_revenue_summary AS
    SELECT
        'jv_deal'::TEXT AS revenue_source,
        date_trunc('month', actual_close_date)::date AS period,
        COUNT(*) AS deal_count,
        SUM(gross_profit) AS gross_profit,
        SUM(platform_earned) AS platform_revenue
    FROM jv_claims
    WHERE status = 'closed'
    GROUP BY period
    UNION ALL
    SELECT
        'direct_sale'::TEXT AS revenue_source,
        date_trunc('month', offer_submitted_at)::date AS period,
        COUNT(*) AS deal_count,
        SUM(gross_profit) AS gross_profit,
        SUM(platform_fee) AS platform_revenue
    FROM direct_purchases
    WHERE contract_status = 'closed'
    GROUP BY period
    ORDER BY period DESC;
