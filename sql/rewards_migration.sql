-- ========================================
-- HYBRID REWARDS + VA REWARDS MIGRATION
-- Backfills 6 tables referenced by app_saas_automated.py
-- (~lines 3470-3665, 4530-4730) that were never created by
-- any prior migration. Reconstructed from every INSERT column
-- list, SELECT column reference, and row['col'] access in that
-- code, plus tier data hardcoded in user_dashboard.html (lines
-- 894-897) for reward_tiers' seed values.
-- ========================================

-- 1. Reward Tiers (lookup/reference table)
-- Fixed "spend $X in the current cycle to claim $Y" ladder used by
-- /api/user/rewards-status and /api/user/claim-reward. tier_level is a
-- plain integer, NOT a hard FK from user_spending_tracker.tier_level,
-- because claim_spending_reward() increments tier_level past the last
-- seeded row once a user maxes out the ladder (the code already treats
-- "tier not found" as "no more tiers", so a FK there would turn that
-- expected state into a 500).
CREATE TABLE IF NOT EXISTS reward_tiers (
    id SERIAL PRIMARY KEY,
    tier_level INTEGER UNIQUE NOT NULL,
    tier_name TEXT NOT NULL,
    spending_required DECIMAL(10,2) NOT NULL,
    reward_amount DECIMAL(10,2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reward_tiers_active ON reward_tiers(is_active);

-- Seed values sourced directly from the tier table hardcoded in
-- user_dashboard.html's loadRewardsPanel() ("Spend $X -> $Y" rows).
INSERT INTO reward_tiers (tier_level, tier_name, spending_required, reward_amount) VALUES
    (0, 'Bronze',   0.00,   5.00),
    (1, 'Silver',   50.00,  10.00),
    (2, 'Gold',     150.00, 20.00),
    (3, 'Platinum', 300.00, 40.00)
ON CONFLICT (tier_level) DO NOTHING;


-- 2. Time Bonus Tiers (lookup/reference table)
-- Multiplier ladder keyed by cumulative active-minutes on the platform;
-- update_user_rewards_tracking() looks up MAX(tier_level) WHERE
-- minutes_required <= total_minutes AND is_active. Only tier 0 ("Explorer",
-- 1.0x, no minutes required) is hard-evidenced by the code's own no-data
-- fallback (get_user_rewards_status' default block). Tier 1's
-- minutes_required/multiplier are inferred from that same fallback's
-- next_time_minutes=180 / next_time_multiplier=1.1 defaults; anything
-- beyond that is not specified anywhere in the codebase, so no further
-- rows are invented here.
CREATE TABLE IF NOT EXISTS time_bonus_tiers (
    id SERIAL PRIMARY KEY,
    tier_level INTEGER UNIQUE NOT NULL,
    tier_name TEXT NOT NULL,
    minutes_required INTEGER NOT NULL,
    multiplier DECIMAL(4,2) NOT NULL DEFAULT 1.00,
    badge TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_time_bonus_tiers_minutes ON time_bonus_tiers(minutes_required);
CREATE INDEX IF NOT EXISTS idx_time_bonus_tiers_active ON time_bonus_tiers(is_active);

INSERT INTO time_bonus_tiers (tier_level, tier_name, minutes_required, multiplier, badge) VALUES
    (0, 'Explorer', 0,   1.00, '🔍'),
    (1, 'Active',   180, 1.10, '⚡')
ON CONFLICT (tier_level) DO NOTHING;


-- 3. VA Rewards
-- Milestone/tier-bonus cash rewards awarded to VAs (check_va_milestones),
-- listed back to the VA via GET /api/va/rewards.
CREATE TABLE IF NOT EXISTS va_rewards (
    id SERIAL PRIMARY KEY,
    va_email TEXT NOT NULL REFERENCES va_users(email) ON DELETE CASCADE,
    reward_type TEXT NOT NULL, -- 'milestone', 'tier_bonus'
    amount DECIMAL(10,2) NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, paid
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_va_rewards_email ON va_rewards(va_email);
CREATE INDEX IF NOT EXISTS idx_va_rewards_email_created ON va_rewards(va_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_va_rewards_status ON va_rewards(status);


-- 4. User Spending Tracker
-- Source of truth for a user's cumulative spend/active-time; written by
-- update_user_rewards_tracking() (the Stripe webhook entry point) and by
-- claim_spending_reward()'s post-claim reset/increment.
CREATE TABLE IF NOT EXISTS user_spending_tracker (
    id SERIAL PRIMARY KEY,
    user_email TEXT UNIQUE NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    total_spent DECIMAL(10,2) NOT NULL DEFAULT 0,
    current_tier_spent DECIMAL(10,2) NOT NULL DEFAULT 0,
    total_time_seconds INTEGER NOT NULL DEFAULT 0,
    tier_level INTEGER NOT NULL DEFAULT 0,
    time_bonus_tier INTEGER NOT NULL DEFAULT 0,
    rewards_claimed_count INTEGER NOT NULL DEFAULT 0,
    last_purchase_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_spending_tracker_tier ON user_spending_tracker(tier_level);


-- 5. User Complete Reward Status
-- Read-only aggregate row (spend + tier name/reward + time-bonus name/
-- multiplier/badge + lifetime points) queried by /api/user/rewards-status
-- and /api/user/claim-reward. Nothing in app_saas_automated.py ever
-- INSERTs/UPDATEs this table by name -- it's computed from
-- user_spending_tracker joined against the two tier lookup tables, so it's
-- a VIEW, not a base table (a plain table here would forever return no
-- rows, since nothing would ever populate it even after a user spends).
-- lifetime_points has no other source of truth in the codebase; modeled
-- as 1 point per dollar spent (floor(total_spent)), the simplest reading
-- consistent with "spend to earn tiers".
CREATE OR REPLACE VIEW user_complete_reward_status AS
    SELECT
        ust.user_email,
        ust.total_spent,
        ust.current_tier_spent,
        ust.tier_level AS spending_tier,
        COALESCE(rt.tier_name, 'Bronze') AS spending_tier_name,
        COALESCE(rt.reward_amount, 5.00) AS base_reward_amount,
        (ust.total_time_seconds / 60) AS total_minutes,
        ust.time_bonus_tier,
        COALESCE(tbt.tier_name, 'Explorer') AS time_bonus_name,
        COALESCE(tbt.multiplier, 1.00) AS time_multiplier,
        COALESCE(tbt.badge, '🔍') AS time_badge,
        ROUND(COALESCE(rt.reward_amount, 5.00) * COALESCE(tbt.multiplier, 1.00), 2) AS boosted_reward_amount,
        tbt_next.minutes_required AS next_time_minutes,
        tbt_next.multiplier AS next_time_multiplier,
        FLOOR(ust.total_spent)::INTEGER AS lifetime_points,
        ust.updated_at
    FROM user_spending_tracker ust
    LEFT JOIN reward_tiers rt ON rt.tier_level = ust.tier_level AND rt.is_active = TRUE
    LEFT JOIN time_bonus_tiers tbt ON tbt.tier_level = ust.time_bonus_tier AND tbt.is_active = TRUE
    LEFT JOIN time_bonus_tiers tbt_next ON tbt_next.tier_level = ust.time_bonus_tier + 1 AND tbt_next.is_active = TRUE;


-- 6. Reward Claims
-- Audit log of each tier-reward claim, written by claim_spending_reward()
-- when a user cashes out a completed spending tier.
CREATE TABLE IF NOT EXISTS reward_claims (
    id SERIAL PRIMARY KEY,
    user_email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    claim_type TEXT NOT NULL DEFAULT 'tier_reward',
    tier_level INTEGER NOT NULL,
    total_spent_at_claim DECIMAL(10,2) NOT NULL,
    points_at_claim INTEGER NOT NULL DEFAULT 0,
    reward_amount DECIMAL(10,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved', -- approved, paid, rejected
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reward_claims_user ON reward_claims(user_email);
CREATE INDEX IF NOT EXISTS idx_reward_claims_created ON reward_claims(created_at DESC);
