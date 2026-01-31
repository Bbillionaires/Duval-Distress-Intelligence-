import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ DATABASE_URL not set")
    exit()

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

print("=" * 70)
print("DATABASE STATUS CHECK")
print("=" * 70)

# Total properties
cur.execute('SELECT COUNT(*) FROM properties')
total = cur.fetchone()[0]
print(f"\n📊 Total properties: {total}")

# By stage
cur.execute('SELECT stage, COUNT(*) FROM properties GROUP BY stage ORDER BY COUNT(*) DESC')
print(f"\n📈 Breakdown by stage:")
for stage, count in cur.fetchall():
    print(f"   {stage:20} {count:5} properties")

# Tax deed notices
cur.execute('SELECT COUNT(*) FROM properties WHERE has_tax_deed_notice = TRUE')
ntd_count = cur.fetchone()[0]
print(f"\n🎯 Properties with Tax Deed Notice: {ntd_count}")

# Sample data
cur.execute('SELECT parcel, owner_name, has_tax_deed_notice, stage FROM properties LIMIT 10')
print(f"\n📋 Sample properties:")
for row in cur.fetchall():
    ntd_flag = "✓" if row[2] else " "
    print(f"   [{ntd_flag}] {row[0]} - {row[1][:30] if row[1] else 'N/A':30} - {row[3]}")

conn.close()
print("\n✅ Check complete!")

# Run database check on startup
if __name__ == "__main__":
    import check_data