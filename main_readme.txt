# 🏘️ Real Estate Intelligence Platform

**Multi-County SaaS for Tax Delinquency Lead Generation**

> One platform. Multiple counties. Scalable revenue.

---

## 🎯 What This Does

Automatically finds and tracks **"sweet spot"** tax-delinquent properties across multiple counties:

- **6-24 months into delinquency** (optimal contact window)
- **Daily automated updates** (always fresh data)
- **Complete distress pipeline** (certificates → notices → auctions)
- **Multi-county support** (add counties = add revenue)

---

## 💰 Revenue Model

### Per-County Pricing

| Tier | Counties | Price | Target Market |
|------|----------|-------|---------------|
| **Tier 1** | Duval, St. Johns, Flagler | $49/mo | Small markets |
| **Tier 2** | Miami-Dade, Broward | $99/mo | Medium markets |
| **Tier 3** | LA County, Cook County | $199/mo | Major markets |

### Package Deals

| Package | Counties | Price | Savings |
|---------|----------|-------|---------|
| **Starter** | 1 county | $49/mo | - |
| **Professional** | 3 counties | $149/mo | $48/mo |
| **Enterprise** | All FL (67) | $399/mo | $2,900/mo |

**Example: 100 Professional subscribers = $14,900/month MRR** 🚀

---

## ✨ Key Features

### For Users
- 🎯 **Sweet Spot Targeting** - Properties 6-24 months into delinquency
- 🔄 **Daily Updates** - Automated scraping, always current
- 📊 **Complete Pipeline** - Track from certificate to auction
- 💾 **Export CSV** - Take data anywhere
- 📈 **Historical Tracking** - See payment patterns over time
- 🚨 **Tax Deed Alerts** - Know when properties hit auction

### For Admins
- 🏛️ **Multi-County Management** - Add counties = add revenue
- 📅 **Automated Scraping** - Set it and forget it
- 💳 **Subscription Management** - Stripe integration
- 📊 **Analytics Dashboard** - Track per-county metrics
- 🔧 **White-Label Ready** - Rebrand for enterprise clients

---

## 🗺️ Supported Counties

### Live Now ✅
- **Duval County, FL** - $49/month

### Coming Soon 🚧
- Miami-Dade County, FL - $99/month
- Broward County, FL - $99/month
- Palm Beach County, FL - $99/month
- Orange County, FL - $79/month
- Hillsborough County, FL - $79/month

### Roadmap 📍
- All Florida counties (67 total)
- Georgia: Fulton, DeKalb, Gwinnett
- Texas: Harris, Dallas, Travis
- California: LA, Orange, San Diego

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│           User Dashboard (React/HTML)           │
│  ┌──────────────┬──────────────┬──────────────┐│
│  │  Duval Data  │ Miami Data   │ Broward Data ││
│  └──────────────┴──────────────┴──────────────┘│
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│              Flask API Server                   │
│  ┌──────────────────────────────────────────┐  │
│  │  /api/properties/{county_code}           │  │
│  │  /api/stats/{county_code}                │  │
│  │  /api/subscribe                          │  │
│  └──────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│           PostgreSQL Database                   │
│  ┌──────────────────────────────────────────┐  │
│  │  users, user_counties, properties        │  │
│  │  (partitioned by county_code)            │  │
│  └──────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│         Automated Scrapers (Cron Jobs)          │
│  ┌──────────┬──────────┬──────────┬─────────┐  │
│  │ Duval    │ Miami    │ Broward  │   ...   │  │
│  │ Scraper  │ Scraper  │ Scraper  │         │  │
│  └──────────┴──────────┴──────────┴─────────┘  │
│         Runs daily at 2 AM per county           │
└─────────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│          External Data Sources                  │
│  • County Tax Collectors (Algolia API)          │
│  • Clerk of Courts (Web Scraping)               │
│  • LienHub (CSV Import)                         │
└─────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Clone & Setup

```bash
git clone https://github.com/yourcompany/real-estate-intelligence.git
cd real-estate-intelligence

# Run setup script
chmod +x setup.sh
./setup.sh
```

### 2. Configure Environment

Edit `.env`:

```bash
DATABASE_URL=postgresql://user:pass@localhost/realestate_intel
ADMIN_EMAIL=admin@yourcompany.com
ADMIN_PASSWORD=SecurePassword123!
STRIPE_API_KEY=sk_test_...
```

### 3. Initialize Database

```bash
python3 -c "from multi_county_architecture import db_init_multi_county; db_init_multi_county()"
```

### 4. Run Development Server

```bash
python app_multi_county.py
```

Visit: `http://localhost:10000`

### 5. Run Test Scrape

```bash
python automated_scraper_multi.py --county duval --test
```

---

## 📦 What's Included

### Core Files
- `multi_county_architecture.py` - County registry & base scrapers
- `app_multi_county.py` - Flask API with county routing
- `automated_scraper_multi.py` - Automated scraping system

### UI Files
- `admin_multi_county.html` - Admin dashboard
- `pricing.html` - Pricing & subscription page
- `login.html` - Authentication
- `index.html` - Landing page

### Documentation
- `DEPLOYMENT_GUIDE.md` - Complete deployment instructions
- `README.md` - This file
- `requirements.txt` - Python dependencies
- `render.yaml` - Auto-deployment config

### County Scrapers
- `counties/duval/scraper.py` - Duval County implementation
- `counties/miami_dade/` - Template for new counties
- `counties/{county_name}/` - Add more counties here

---

## 🎨 Adding a New County

### 1. Add County Configuration

```python
# multi_county_architecture.py

COUNTIES["new-county"] = CountyConfig(
    code="new-county",
    name="New County",
    state="FL",
    tax_collector_url="https://...",
    clerk_url="https://...",
    pricing_tier=2,  # $99/month
    enabled=True
)
```

### 2. Create Scraper

```python
# counties/new_county/scraper.py

class NewCountyScraper(CountyScraper):
    def scrape_sweet_spot_leads(self):
        # County-specific scraping logic
        pass
```

### 3. Test

```bash
python automated_scraper_multi.py --county new-county --test
```

### 4. Deploy

```bash
git add .
git commit -m "Add New County"
git push
```

Render auto-deploys! 🚀

---

## 💻 Tech Stack

### Backend
- **Python 3.11** - Core language
- **Flask** - Web framework
- **PostgreSQL** - Database
- **Psycopg2** - Database driver
- **Requests** - HTTP client
- **BeautifulSoup** - Web scraping

### Frontend
- **HTML/CSS/JavaScript** - UI
- **Tailwind CSS** - Styling (optional)
- **Vanilla JS** - No framework needed

### Infrastructure
- **Render.com** - Hosting ($14/month)
- **Stripe** - Payments
- **SendGrid** - Emails (optional)
- **Sentry** - Error tracking (optional)

---

## 📊 Sample Data Flow

### User Subscribes to Miami-Dade

```
1. User selects Professional package ($149/mo)
2. Stripe processes payment
3. Webhook grants access to 3 counties
4. User sees Miami-Dade data instantly
```

### Automated Daily Scrape

```
2:00 AM - Duval scraper starts
2:01 AM - Loads certificate data from CSV
2:02 AM - Verifies 1,000 properties (live amounts)
2:15 AM - Identifies 450 sweet spot leads
2:16 AM - Stores in database
2:17 AM - Scrapes tax deed notices
2:20 AM - Job complete

3:00 AM - Miami-Dade scraper starts
...
```

---

## 💳 Stripe Integration

### Setup

```python
import stripe
stripe.api_key = os.getenv("STRIPE_API_KEY")

# Create checkout session
session = stripe.checkout.Session.create(
    payment_method_types=['card'],
    line_items=[{
        'price': 'price_professional',  # From Stripe dashboard
        'quantity': 1,
    }],
    mode='subscription',
    success_url='https://yourapp.com/success',
)
```

### Webhooks

Handle subscription events:
- `customer.subscription.created` → Grant county access
- `customer.subscription.deleted` → Revoke access
- `invoice.payment_failed` → Send dunning email

---

## 📈 Growth Strategy

### Phase 1: Validate (Months 1-3)
- Launch with Duval County only
- Target: 20-50 paying users
- Goal: Prove product-market fit
- **MRR: $1K-$2.5K**

### Phase 2: Expand Florida (Months 4-9)
- Add Miami-Dade, Broward, Palm Beach
- Launch Professional package
- Target: 100-200 users
- **MRR: $10K-$25K**

### Phase 3: Go Multi-State (Year 2)
- Georgia, Texas, California
- Enterprise tier for large firms
- White-label option
- **MRR: $50K-$100K**

### Phase 4: National Scale (Year 3+)
- All 50 states
- 3,000+ counties available
- API marketplace
- **MRR: $200K+**

---

## 🔒 Security

- ✅ Password hashing (Werkzeug)
- ✅ Secure sessions (HttpOnly cookies)
- ✅ SQL injection prevention (parameterized queries)
- ✅ HTTPS enforced (Render)
- ✅ Rate limiting on API endpoints
- ✅ Admin-only scraping endpoints
- ✅ County access control per user

---

## 🐛 Troubleshooting

### Database connection failed
```bash
# Check DATABASE_URL is set
echo $DATABASE_URL

# Test connection
psql $DATABASE_URL -c "SELECT 1"
```

### Scraper not running
```bash
# Check cron jobs in Render dashboard
# View logs for errors
# Test manually:
python automated_scraper_multi.py --county duval
```

### No properties found
```bash
# Verify CSV file exists
ls -lh duval_lienhub_x_live_zip_v3.csv

# Check Algolia credentials
# Test API manually
```

---

## 📚 Documentation

- **[DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)** - Full deployment guide
- **[API_DOCS.md](./API_DOCS.md)** - API reference
- **[COUNTY_GUIDE.md](./COUNTY_GUIDE.md)** - Adding new counties
- **[STRIPE_SETUP.md](./STRIPE_SETUP.md)** - Payment integration

---

## 🤝 Contributing

### Adding a New County

1. Fork the repository
2. Create county scraper: `counties/your_county/scraper.py`
3. Add tests: `tests/test_your_county.py`
4. Submit pull request

### Reporting Issues

Use GitHub Issues with:
- County code (if relevant)
- Steps to reproduce
- Expected vs actual behavior
- Error logs

---

## 📞 Support

**Email:** support@realestateintel.com  
**Docs:** https://docs.realestateintel.com  
**Status:** https://status.realestateintel.com

---

## 📄 License

Proprietary - All Rights Reserved

This software is for authorized users only. Unauthorized copying, distribution, or modification is prohibited.

---

## 🎉 Ready to Launch?

```bash
# 1. Setup
./setup.sh

# 2. Configure
nano .env

# 3. Initialize database
python -c "from multi_county_architecture import db_init_multi_county; db_init_multi_county()"

# 4. Test locally
python app_multi_county.py

# 5. Deploy to production
git push origin main
# Render auto-deploys!

# 6. Start making money! 💰
```

---

**Built with ❤️ for real estate investors**

*One platform. Multiple counties. Unlimited potential.* 🚀
