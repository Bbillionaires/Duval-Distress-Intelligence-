-- service_pricing is read/written throughout the payment + VA-payout flow
-- (create_payment_intent, stripe_webhook, api_get_active_pricing,
-- api_user_create_service_request, api_admin_get/update/toggle_pricing)
-- but no migration ever created it, so every one of those endpoints 500s.
-- This left the Stripe payment-intent step failing before checkout even
-- starts (create_payment_intent 500s on the missing-table SELECT), so no
-- charge could complete without a request record backing it up.
CREATE TABLE IF NOT EXISTS service_pricing (
    id SERIAL PRIMARY KEY,
    service_type TEXT UNIQUE NOT NULL,
    service_name TEXT NOT NULL,
    service_description TEXT,
    price_charged NUMERIC(10,2) NOT NULL,
    va_payout NUMERIC(10,2) NOT NULL,
    display_order INT DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_service_pricing_active ON service_pricing(active);

-- Seed with the same 4 services user_dashboard.html already hardcodes as
-- its offline fallback, so real pricing now matches what the UI has been
-- silently falling back to. va_payout is a placeholder (60% of price) --
-- adjust via the pricing admin page.
INSERT INTO service_pricing (service_type, service_name, service_description, price_charged, va_payout, display_order) VALUES
    ('skiptrace', 'Skip Trace', 'Find owner contact info', 15.00, 9.00, 1),
    ('cold_call_block', 'Cold Call Block', '50 calls to motivated sellers', 45.00, 27.00, 2),
    ('postcard', 'Postcard Campaign', 'Direct mail to owner', 12.00, 7.00, 3),
    ('door_knock', 'Door Knock', 'In-person property visit', 35.00, 21.00, 4)
ON CONFLICT (service_type) DO NOTHING;

-- create_payment_intent / stripe_webhook read+write a tip amount on top of
-- the service price, but service_requests never got this column.
ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS tip_amount NUMERIC(10,2) DEFAULT 0;
