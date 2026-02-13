#!/usr/bin/env python3
"""
User Statistics Script

Displays current user count and recent registrations from Supabase.
Uses the Admin API (auth.users) to get all users, not just user_profiles.

Usage:
  python scripts/check_users.py
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Load environment variables from .env.shared
env_path = os.path.join(os.path.dirname(__file__), '..', '.env.shared')
load_dotenv(env_path)

if not os.getenv('SUPABASE_URL'):
    env_path_parent = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(env_path_parent)

SUPABASE_URL = os.getenv('SUPABASE_URL') or os.getenv('VITE_SUPABASE_URL')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    print("Set them in .env.shared or as environment variables")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def parse_dt(s):
    """Parse ISO datetime string or datetime object to timezone-aware datetime."""
    if not s:
        return None
    if isinstance(s, datetime):
        if s.tzinfo is None:
            return s.replace(tzinfo=timezone.utc)
        return s
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def get_user_stats():
    # Get all auth users via Admin API (paginates automatically)
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

    # Get user_profiles for test account info
    profiles_result = supabase.table('user_profiles').select('id, is_test_account').execute()
    profile_map = {p['id']: p for p in profiles_result.data}

    # Sort by created_at descending
    all_users.sort(key=lambda u: getattr(u, 'created_at', '') or '', reverse=True)

    total = len(all_users)
    test_accounts = sum(1 for u in all_users if profile_map.get(getattr(u, 'id', ''), {}).get('is_test_account'))
    real_accounts = total - test_accounts
    has_profile = sum(1 for u in all_users if getattr(u, 'id', '') in profile_map)

    print("=" * 70)
    print("  FeedOwn User Statistics")
    print("=" * 70)
    print()
    print(f"  Total auth users:     {total}")
    print(f"  With user_profile:    {has_profile}")
    print(f"  Without profile:      {total - has_profile}")
    print(f"  Test accounts:        {test_accounts}")
    print(f"  Real accounts:        {real_accounts}")
    print()

    # Recent registrations
    now = datetime.now(timezone.utc)
    periods = [
        ("Last 24 hours", timedelta(days=1)),
        ("Last 7 days", timedelta(days=7)),
        ("Last 30 days", timedelta(days=30)),
    ]

    print("-" * 70)
    print("  Recent Registrations")
    print("-" * 70)
    for label, delta in periods:
        cutoff = now - delta
        count = sum(1 for u in all_users
                    if parse_dt(getattr(u, 'created_at', None)) and parse_dt(getattr(u, 'created_at', None)) >= cutoff)
        print(f"  {label:<20s} {count} users")
    print()

    # Per-user stats: feeds and articles count
    feeds_result = supabase.table('feeds').select('user_id').execute()
    articles_result = supabase.table('articles').select('user_id').execute()

    feed_counts = {}
    for f in feeds_result.data:
        uid = f['user_id']
        feed_counts[uid] = feed_counts.get(uid, 0) + 1

    article_counts = {}
    for a in articles_result.data:
        uid = a['user_id']
        article_counts[uid] = article_counts.get(uid, 0) + 1

    print("-" * 70)
    print("  Database Summary")
    print("-" * 70)
    print(f"  Total feeds:      {len(feeds_result.data)}")
    print(f"  Total articles:   {len(articles_result.data)}")
    print()

    # Show all users
    print("-" * 70)
    print("  All Users (newest first)")
    print("-" * 70)
    print(f"  {'#':<4} {'Email':<35} {'Feeds':<7} {'Articles':<10} {'Profile':<9} {'Last Sign In'}")
    print(f"  {'─'*4} {'─'*35} {'─'*7} {'─'*10} {'─'*9} {'─'*19}")

    for i, user in enumerate(all_users, 1):
        uid = getattr(user, 'id', '')
        email = getattr(user, 'email', 'N/A') or 'N/A'
        feeds = feed_counts.get(uid, 0)
        articles = article_counts.get(uid, 0)
        has_prof = 'Yes' if uid in profile_map else ''
        last_sign_in = ''
        raw = getattr(user, 'last_sign_in_at', None)
        if raw:
            dt = parse_dt(raw)
            if dt:
                last_sign_in = dt.strftime('%Y-%m-%d %H:%M')
        created = ''
        raw_c = getattr(user, 'created_at', None)
        if raw_c:
            dt_c = parse_dt(raw_c)
            if dt_c:
                created = dt_c.strftime('%Y-%m-%d %H:%M')

        print(f"  {i:<4} {email:<35} {feeds:<7} {articles:<10} {has_prof:<9} {last_sign_in}")

    print()
    print("=" * 70)


if __name__ == '__main__':
    get_user_stats()
