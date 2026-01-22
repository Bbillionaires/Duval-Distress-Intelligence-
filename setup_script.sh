#!/bin/bash

# Real Estate Intelligence Platform - Quick Setup Script
# Run this to set up your development environment

set -e  # Exit on error

echo "🏘️  Real Estate Intelligence Platform"
echo "======================================"
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version || {
    echo "❌ Python 3 not found. Please install Python 3.9+"
    exit 1
}

# Create virtual environment
echo ""
echo "Creating virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "ℹ️  Virtual environment already exists"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate || source venv/Scripts/activate

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt
echo "✅ Dependencies installed"

# Create .env file if it doesn't exist
echo ""
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    cat > .env << EOF
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/realestate_intel

# App Configuration
APP_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
PORT=10000

# Admin Account
ADMIN_EMAIL=admin@local
ADMIN_PASSWORD=ChangeMe123!

# Stripe (get from https://dashboard.stripe.com)
STRIPE_API_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Optional: SendGrid for emails
SENDGRID_API_KEY=

# Optional: Sentry for error tracking
SENTRY_DSN=
EOF
    echo "✅ .env file created"
    echo "⚠️  IMPORTANT: Edit .env and add your database URL"
else
    echo "ℹ️  .env file already exists"
fi

# Create directories
echo ""
echo "Creating directory structure..."
mkdir -p counties/duval
mkdir -p counties/miami_dade
mkdir -p tax_leads_complete
mkdir -p snapshots
mkdir -p static
mkdir -p templates
echo "✅ Directories created"

# Create a sample county scraper if it doesn't exist
if [ ! -f "counties/duval/scraper.py" ]; then
    echo ""
    echo "Creating sample Duval scraper..."
    cat > counties/duval/scraper.py << 'EOF'
"""
Duval County Scraper
"""
from base_scraper import CountyScraper

class DuvalScraper(CountyScraper):
    def scrape_sweet_spot_leads(self):
        # Implement Duval-specific scraping
        print(f"Scraping {self.county.name}...")
        pass
    
    def scrape_tax_deed_notices(self):
        # Implement NTD scraping
        pass
EOF
    echo "✅ Sample scraper created"
fi

# Check if PostgreSQL is running (optional)
echo ""
echo "Checking PostgreSQL..."
if command -v psql &> /dev/null; then
    echo "✅ PostgreSQL client found"
    echo "ℹ️  Make sure PostgreSQL server is running"
    echo "ℹ️  Update DATABASE_URL in .env"
else
    echo "⚠️  PostgreSQL client not found"
    echo "ℹ️  You can use a cloud database (Render, Supabase, etc.)"
fi

# Test imports
echo ""
echo "Testing Python imports..."
python3 << EOF
try:
    import flask
    import psycopg2
    import requests
    import bs4
    print("✅ All required packages imported successfully")
except ImportError as e:
    print(f"❌ Import error: {e}")
    exit(1)
EOF

# Summary
echo ""
echo "======================================"
echo "✅ Setup Complete!"
echo "======================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Edit .env file with your database credentials"
echo "2. Initialize database:"
echo "   python3 -c 'from multi_county_architecture import db_init_multi_county; db_init_multi_county()'"
echo ""
echo "3. Run the development server:"
echo "   python app_multi_county.py"
echo ""
echo "4. Visit: http://localhost:10000"
echo ""
echo "5. Login with:"
echo "   Email: admin@local"
echo "   Password: ChangeMe123!"
echo ""
echo "======================================"
echo ""
echo "📚 Documentation:"
echo "   - DEPLOYMENT_GUIDE.md - Full deployment instructions"
echo "   - README.md - Platform overview"
echo ""
echo "💰 Pricing Strategy:"
echo "   - Duval County: $49/month (LIVE)"
echo "   - Add more counties to scale revenue"
echo ""
echo "🚀 Deploy to production:"
echo "   1. Push to GitHub"
echo "   2. Connect to Render.com"
echo "   3. Render auto-deploys from render.yaml"
echo ""
echo "Questions? Check the documentation or contact support."
echo ""
