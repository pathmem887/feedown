#!/usr/bin/env python3
"""
Supabase Free Tier Usage Checker

Checks current usage against Supabase free tier limits.

Usage:
  python scripts/check_usage.py

Optional: Set SUPABASE_ACCESS_TOKEN for detailed DB size info.
  Get it from: https://supabase.com/dashboard/account/tokens
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

try:
    import requests
except ImportError:
    print("Error: requests library not installed")
    print("Run: pip install requests")
    sys.exit(1)

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Load environment variables
env_path = os.path.join(os.path.dirname(__file__), '..', '.env.shared')
load_dotenv(env_path)

if not os.getenv('SUPABASE_URL'):
    env_path_parent = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(env_path_parent)

SUPABASE_URL = os.getenv('SUPABASE_URL') or os.getenv('VITE_SUPABASE_URL')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
SUPABASE_ACCESS_TOKEN = os.getenv('SUPABASE_ACCESS_TOKEN')  # Optional: personal access token

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    sys.exit(1)

# Extract project ref from URL (e.g., https://abcdef.supabase.co -> abcdef)
PROJECT_REF = SUPABASE_URL.replace('https://', '').split('.')[0]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ── Supabase Free Tier Limits ──────────────────────────────────────
FREE_TIER = {
    'db_size_mb': 500,
    'auth_mau': 50_000,
    'storage_gb': 1,
    'realtime_concurrent': 200,
    'edge_function_invocations': 500_000,
    'bandwidth_gb': 5,
}


def format_bytes(b):
    """Format bytes to human-readable string."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    else:
        return f"{b / 1024 ** 3:.2f} GB"


def progress_bar(pct, width=30):
    """Create a text progress bar."""
    filled = int(width * min(pct, 100) / 100)
    bar = '#' * filled + '-' * (width - filled)
    if pct >= 90:
        status = '!!!'
    elif pct >= 70:
        status = '!'
    else:
        status = ''
    return f"[{bar}] {pct:5.1f}% {status}"


def get_auth_stats():
    """Get auth user statistics."""
    all_users = []
    page = 1
    per_page = 100
    while True:
        res = supabase.auth.admin.list_users(page=page, per_page=per_page)
        batch = res if isinstance(res, list) else getattr(res, 'users', res)
        if not batch:
            break
        all_users.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    total = len(all_users)

    # Count MAU (users who signed in within last 30 days)
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)
    mau = 0
    for u in all_users:
        last_sign_in = getattr(u, 'last_sign_in_at', None)
        if last_sign_in:
            if isinstance(last_sign_in, datetime):
                dt = last_sign_in if last_sign_in.tzinfo else last_sign_in.replace(tzinfo=timezone.utc)
            else:
                try:
                    dt = datetime.fromisoformat(str(last_sign_in).replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    continue
            if dt >= cutoff_30d:
                mau += 1

    return total, mau


def get_table_row_counts():
    """Get row counts for all application tables."""
    tables = ['user_profiles', 'feeds', 'articles', 'read_articles', 'favorites', 'recommended_feeds']
    counts = {}
    for table in tables:
        try:
            result = supabase.table(table).select('*', count='exact').limit(0).execute()
            counts[table] = result.count if result.count is not None else 0
        except Exception as e:
            counts[table] = f"Error: {e}"
    return counts


def get_db_size_via_management_api():
    """Try to get DB size via Supabase Management API."""
    if not SUPABASE_ACCESS_TOKEN:
        return None

    headers = {
        'Authorization': f'Bearer {SUPABASE_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }

    # Try multiple endpoints to find DB size
    endpoints = [
        f'/v1/projects/{PROJECT_REF}/usage',
        f'/v1/projects/{PROJECT_REF}/database/usage',
        f'/v1/projects/{PROJECT_REF}/readonly/database/size',
    ]

    for endpoint in endpoints:
        try:
            url = f'https://api.supabase.com{endpoint}'
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and data != {}:
                    return {'endpoint': endpoint, 'data': data}
        except Exception:
            pass

    # Fallback: get project info and extract any size-related fields
    try:
        url = f'https://api.supabase.com/v1/projects/{PROJECT_REF}'
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            proj = resp.json()
            # Extract useful fields
            return {
                'endpoint': 'project_info',
                'data': {
                    'name': proj.get('name'),
                    'region': proj.get('region'),
                    'status': proj.get('status'),
                    'database': proj.get('database', {}),
                }
            }
    except Exception:
        pass

    return None


def get_db_size_via_rpc():
    """Try to get DB size by calling pg_database_size via PostgREST RPC."""
    # This requires a function to exist. Try common approaches.
    headers = {
        'apikey': SUPABASE_SERVICE_ROLE_KEY,
        'Authorization': f'Bearer {SUPABASE_SERVICE_ROLE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
    }

    # Try raw SQL via PostgREST (won't work without a function, but worth trying)
    try:
        url = f'{SUPABASE_URL}/rest/v1/rpc/pg_database_size'
        resp = requests.post(url, headers=headers, json={'db_name': 'postgres'}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass

    return None


def estimate_table_sizes(row_counts):
    """Estimate table sizes based on row counts and average row sizes."""
    # Rough estimates of average row size in bytes (based on schema)
    avg_row_sizes = {
        'user_profiles': 200,       # UUID + email + booleans + timestamp
        'feeds': 500,               # UUID + URLs + titles + timestamps
        'articles': 800,            # hash + UUIDs + text fields + timestamps
        'read_articles': 100,       # 2 UUIDs + timestamp
        'favorites': 600,           # hash + UUID + text fields + timestamp
        'recommended_feeds': 300,   # UUID + name + URL + metadata
    }

    total_bytes = 0
    table_sizes = {}
    for table, count in row_counts.items():
        if isinstance(count, int):
            avg = avg_row_sizes.get(table, 300)
            size = count * avg
            # Add ~30% overhead for indexes
            size_with_idx = int(size * 1.3)
            table_sizes[table] = size_with_idx
            total_bytes += size_with_idx
        else:
            table_sizes[table] = 0

    return table_sizes, total_bytes


def main():
    print()
    print("=" * 70)
    print("  FeedOwn - Supabase Free Tier Usage Report")
    print("=" * 70)
    print(f"  Project: {PROJECT_REF}")
    print(f"  Date:    {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    # ── 1. Auth Users ──────────────────────────────────────────────
    print("-" * 70)
    print("  1. Authentication (MAU)")
    print("-" * 70)

    total_users, mau = get_auth_stats()
    mau_limit = FREE_TIER['auth_mau']
    mau_pct = (mau / mau_limit) * 100

    print(f"  Total registered users:  {total_users}")
    print(f"  Monthly Active Users:    {mau}")
    print(f"  Free tier limit:         {mau_limit:,}")
    print(f"  Usage: {progress_bar(mau_pct)}")
    print()

    # ── 2. Database Rows ───────────────────────────────────────────
    print("-" * 70)
    print("  2. Database Tables")
    print("-" * 70)

    row_counts = get_table_row_counts()
    total_rows = 0

    print(f"  {'Table':<25} {'Rows':>10} {'Est. Size':>12}")
    print(f"  {'─'*25} {'─'*10} {'─'*12}")

    table_sizes, total_est_bytes = estimate_table_sizes(row_counts)

    for table, count in row_counts.items():
        if isinstance(count, int):
            total_rows += count
            size_str = format_bytes(table_sizes.get(table, 0))
            print(f"  {table:<25} {count:>10,} {size_str:>12}")
        else:
            print(f"  {table:<25} {'Error':>10}")

    print(f"  {'─'*25} {'─'*10} {'─'*12}")
    print(f"  {'TOTAL':<25} {total_rows:>10,} {format_bytes(total_est_bytes):>12}")
    print()

    # ── 3. Database Size ───────────────────────────────────────────
    print("-" * 70)
    print("  3. Database Size")
    print("-" * 70)

    db_limit_mb = FREE_TIER['db_size_mb']
    db_limit_bytes = db_limit_mb * 1024 * 1024

    # Try Management API
    db_info = get_db_size_via_management_api()
    actual_size = None

    if db_info and isinstance(db_info, dict):
        data = db_info.get('data', db_info)

        # Try to extract DB size from various response formats
        if isinstance(data, list):
            # Usage endpoint returns a list of metrics
            for item in data:
                metric = item.get('metric', item.get('name', ''))
                if 'db_size' in str(metric).lower() or 'database' in str(metric).lower():
                    actual_size = item.get('usage', item.get('value', item.get('total', 0)))
                    break
        elif isinstance(data, dict):
            # Try common field names
            for key in ['db_size', 'database_size', 'disk_usage', 'total_size']:
                if key in data:
                    actual_size = data[key]
                    break
            # Check nested database object
            db_obj = data.get('database', {})
            if isinstance(db_obj, dict):
                for key in ['size', 'disk_usage', 'used']:
                    if key in db_obj:
                        actual_size = db_obj[key]
                        break

    if actual_size and isinstance(actual_size, (int, float)) and actual_size > 0:
        db_pct = (actual_size / db_limit_bytes) * 100
        print(f"  Database size (actual):   {format_bytes(int(actual_size))}")
        print(f"  Free tier limit:          {db_limit_mb} MB")
        print(f"  Usage: {progress_bar(db_pct)}")
    else:
        # Use estimate
        est_pct = (total_est_bytes / db_limit_bytes) * 100
        print(f"  Database size (estimate): {format_bytes(total_est_bytes)}")
        print(f"  Free tier limit:          {db_limit_mb} MB")
        print(f"  Usage: {progress_bar(est_pct)}  (estimated)")
        if db_info:
            endpoint = db_info.get('endpoint', 'unknown')
            print(f"  (Management API responded via {endpoint} but DB size field not found)")
        if not SUPABASE_ACCESS_TOKEN:
            print()
            print("  Tip: Set SUPABASE_ACCESS_TOKEN in .env.shared for actual DB size.")
            print("  Get it from: https://supabase.com/dashboard/account/tokens")

    print()

    # ── 4. Growth Projection ───────────────────────────────────────
    print("-" * 70)
    print("  4. Growth Projection")
    print("-" * 70)

    articles_count = row_counts.get('articles', 0)
    feeds_count = row_counts.get('feeds', 0)

    if isinstance(articles_count, int) and isinstance(feeds_count, int) and feeds_count > 0:
        avg_articles_per_feed = articles_count / feeds_count if feeds_count else 0
        print(f"  Avg articles per feed:    {avg_articles_per_feed:.1f}")
        print(f"  Articles TTL:             7 days")
        print()

        # Estimate max DB size at different user counts
        print(f"  {'Users':<10} {'Feeds (est)':<15} {'Articles (est)':<18} {'DB Size (est)':<15} {'% of 500MB'}")
        print(f"  {'─'*10} {'─'*15} {'─'*18} {'─'*15} {'─'*10}")

        for user_count in [10, 50, 100, 500, 1000]:
            est_feeds = user_count * (feeds_count / max(total_users, 1))
            est_articles = est_feeds * avg_articles_per_feed
            # Estimate size: articles ~800B, feeds ~500B, read_articles ~100B per article, overhead 30%
            est_size = int((est_articles * 800 + est_feeds * 500 + est_articles * 100) * 1.3)
            pct = (est_size / db_limit_bytes) * 100
            warning = ' !!!' if pct >= 90 else ' !' if pct >= 70 else ''
            print(f"  {user_count:<10} {est_feeds:<15.0f} {est_articles:<18.0f} {format_bytes(est_size):<15} {pct:.1f}%{warning}")
    print()

    # ── 5. Summary ─────────────────────────────────────────────────
    print("-" * 70)
    print("  5. Summary")
    print("-" * 70)

    checks = []

    # Auth check
    if mau_pct < 70:
        checks.append(('Auth (MAU)', 'OK', f'{mau}/{mau_limit:,}'))
    elif mau_pct < 90:
        checks.append(('Auth (MAU)', 'WARNING', f'{mau}/{mau_limit:,}'))
    else:
        checks.append(('Auth (MAU)', 'CRITICAL', f'{mau}/{mau_limit:,}'))

    # DB size check
    est_pct = (total_est_bytes / db_limit_bytes) * 100
    if est_pct < 70:
        checks.append(('Database Size', 'OK', f'~{format_bytes(total_est_bytes)}/{db_limit_mb}MB'))
    elif est_pct < 90:
        checks.append(('Database Size', 'WARNING', f'~{format_bytes(total_est_bytes)}/{db_limit_mb}MB'))
    else:
        checks.append(('Database Size', 'CRITICAL', f'~{format_bytes(total_est_bytes)}/{db_limit_mb}MB'))

    # Row count check (articles with TTL should stay manageable)
    if articles_count > 50000:
        checks.append(('Article Rows', 'WARNING', f'{articles_count:,} rows (TTL 7d should clean up)'))
    else:
        checks.append(('Article Rows', 'OK', f'{articles_count:,} rows'))

    for name, status, detail in checks:
        icon = {'OK': '[OK]', 'WARNING': '[!!]', 'CRITICAL': '[XX]'}[status]
        print(f"  {icon} {name:<20} {detail}")

    print()

    # Overall verdict
    statuses = [s for _, s, _ in checks]
    if 'CRITICAL' in statuses:
        print("  >>> VERDICT: Approaching free tier limits! Consider upgrading. <<<")
    elif 'WARNING' in statuses:
        print("  >>> VERDICT: Within limits, but monitor growth. <<<")
    else:
        print("  >>> VERDICT: Comfortably within free tier limits. <<<")

    print()
    print("=" * 70)


if __name__ == '__main__':
    main()
