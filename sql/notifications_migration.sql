-- In-app notification center for users/VAs/admins. user_dashboard.html and
-- va_portal.html both call this unconditionally on page load and poll it
-- every 60s, so this 500'd on every dashboard/portal load for every
-- logged-in user until this table existed.
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    user_type TEXT,               -- 'user', 'admin', or 'va'
    title TEXT NOT NULL,
    message TEXT,
    type TEXT DEFAULT 'info',     -- 'info', 'success', 'warning', 'error'
    link TEXT,                    -- optional click-through URL
    read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_email ON notifications(user_email);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at DESC);
