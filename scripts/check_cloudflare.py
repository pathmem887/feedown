#!/usr/bin/env python3
"""
Cloudflare Free Tier Usage Checker

Checks current usage against Cloudflare free tier limits.

Usage:
  python scripts/check_cloudflare.py

Required environment variable:
  CLOUDFLARE_API_TOKEN - API token with read access
    Create at: https://dash.cloudflare.com/profile/api-tokens
    Template: "Read all resources" or custom with:
      - Account.Workers Scripts: Read
      - Account.Workers KV Storage: Read
      - Account.Cloudflare Pages: Read
      - Account.Account Analytics: Read
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

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

if not os.getenv('CLOUDFLARE_API_TOKEN'):
    env_path_parent = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(env_path_parent)

CLOUDFLARE_API_TOKEN = os.getenv('CLOUDFLARE_API_TOKEN')
ACCOUNT_ID = os.getenv('CLOUDFLARE_ACCOUNT_ID', 'ee20aa897421e01cb195b1e36acebf02')

CF_API = 'https://api.cloudflare.com/client/v4'

if not CLOUDFLARE_API_TOKEN:
    print("Error: CLOUDFLARE_API_TOKEN is required")
    print()
    print("Create an API token at: https://dash.cloudflare.com/profile/api-tokens")
    print("Recommended: Use the 'Read all resources' template")
    print()
    print("Then add to .env.shared:")
    print("  CLOUDFLARE_API_TOKEN=your-token-here")
    sys.exit(1)

# ── Cloudflare Free Tier Limits ────────────────────────────────────
FREE_TIER = {
    'workers_requests_per_day': 100_000,
    'workers_cpu_ms_per_invocation': 10,
    'kv_reads_per_day': 100_000,
    'kv_writes_per_day': 1_000,
    'kv_storage_mb': 1_000,       # 1 GB
    'kv_keys_max': 1_000_000_000,  # 1 billion
    'pages_deployments_per_month': 500,
    'pages_functions_per_day': 100_000,
    'pages_bandwidth_unlimited': True,
}


def cf_get(path, params=None):
    """Make an authenticated GET request to the Cloudflare API."""
    headers = {
        'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
        'Content-Type': 'application/json',
    }
    url = f'{CF_API}{path}'
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    return resp.status_code, resp.json()


def cf_post(path, data=None):
    """Make an authenticated POST request to the Cloudflare API."""
    headers = {
        'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
        'Content-Type': 'application/json',
    }
    url = f'{CF_API}{path}'
    resp = requests.post(url, headers=headers, json=data, timeout=30)
    return resp.status_code, resp.json()


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


def format_num(n):
    """Format number with commas."""
    if isinstance(n, (int, float)):
        return f"{n:,.0f}"
    return str(n)


def check_token():
    """Verify the API token works."""
    status, data = cf_get('/user/tokens/verify')
    if status != 200 or not data.get('success'):
        print(f"  Error: API token verification failed")
        errors = data.get('errors', [])
        for e in errors:
            print(f"    {e.get('message', e)}")
        sys.exit(1)
    return True


def get_workers_analytics():
    """Get Workers analytics using GraphQL API."""
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')

    # Use GraphQL analytics API
    headers = {
        'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
        'Content-Type': 'application/json',
    }

    query = """
    query {
      viewer {
        accounts(filter: {accountTag: "%s"}) {
          workersInvocationsAdaptive(
            filter: {
              datetimeHour_geq: "%sT00:00:00Z"
              datetimeHour_leq: "%sT23:59:59Z"
            }
            limit: 1000
            orderBy: [datetimeHour_ASC]
          ) {
            sum {
              requests
              subrequests
              errors
            }
            dimensions {
              datetimeHour
              scriptName
            }
          }
        }
      }
    }
    """ % (ACCOUNT_ID, week_ago, today)

    resp = requests.post(
        'https://api.cloudflare.com/client/v4/graphql',
        headers=headers,
        json={'query': query},
        timeout=30,
    )

    if resp.status_code == 200:
        return resp.json()
    return None


def get_pages_projects():
    """Get Pages projects and deployment info."""
    status, data = cf_get(f'/accounts/{ACCOUNT_ID}/pages/projects')
    if status == 200 and data.get('success'):
        return data.get('result', [])
    return None


def get_pages_deployments(project_name):
    """Get recent deployments for a Pages project."""
    status, data = cf_get(
        f'/accounts/{ACCOUNT_ID}/pages/projects/{project_name}/deployments',
        params={'per_page': 50}
    )
    if status == 200 and data.get('success'):
        return data.get('result', [])
    return None


def get_kv_namespaces():
    """Get KV namespaces and their stats."""
    status, data = cf_get(f'/accounts/{ACCOUNT_ID}/storage/kv/namespaces')
    if status == 200 and data.get('success'):
        return data.get('result', [])
    return None


def get_workers_list():
    """Get list of Workers scripts."""
    status, data = cf_get(f'/accounts/{ACCOUNT_ID}/workers/scripts')
    if status == 200 and data.get('success'):
        return data.get('result', [])
    return None


def get_pages_functions_analytics():
    """Get Pages Functions analytics using GraphQL API (httpRequests1dGroups)."""
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')

    headers = {
        'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
        'Content-Type': 'application/json',
    }

    # Pages Functions use a different analytics dataset
    query = """
    query {
      viewer {
        accounts(filter: {accountTag: "%s"}) {
          pagesFunctionsInvocationsAdaptiveGroups(
            filter: {
              date_geq: "%s"
              date_leq: "%s"
            }
            limit: 1000
            orderBy: [date_ASC]
          ) {
            sum {
              requests
              errors
            }
            dimensions {
              date
              scriptName
            }
          }
        }
      }
    }
    """ % (ACCOUNT_ID, week_ago, today)

    resp = requests.post(
        'https://api.cloudflare.com/client/v4/graphql',
        headers=headers,
        json={'query': query},
        timeout=30,
    )

    if resp.status_code == 200:
        result = resp.json()
        # If the above dataset doesn't exist, try alternative
        if result.get('errors'):
            return get_pages_functions_analytics_alt()
        return result
    return None


def get_pages_functions_analytics_alt():
    """Alternative: try workersInvocationsAdaptive which may include Pages Functions."""
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')

    headers = {
        'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
        'Content-Type': 'application/json',
    }

    # Try broader query that may include Pages Functions
    query = """
    query {
      viewer {
        accounts(filter: {accountTag: "%s"}) {
          workersInvocationsAdaptive(
            filter: {
              datetimeHour_geq: "%sT00:00:00Z"
              datetimeHour_leq: "%sT23:59:59Z"
            }
            limit: 10000
            orderBy: [datetimeHour_ASC]
          ) {
            sum {
              requests
              errors
            }
            dimensions {
              datetimeHour
              scriptName
            }
          }
        }
      }
    }
    """ % (ACCOUNT_ID, week_ago, today)

    resp = requests.post(
        'https://api.cloudflare.com/client/v4/graphql',
        headers=headers,
        json={'query': query},
        timeout=30,
    )

    if resp.status_code == 200:
        return resp.json()
    return None


def main():
    print()
    print("=" * 70)
    print("  FeedOwn - Cloudflare Free Tier Usage Report")
    print("=" * 70)
    print(f"  Account ID: {ACCOUNT_ID}")
    print(f"  Date:       {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    # Verify token
    print("  Verifying API token...", end=" ")
    check_token()
    print("OK")
    print()

    # ── 1. Workers ─────────────────────────────────────────────────
    print("-" * 70)
    print("  1. Workers")
    print("-" * 70)

    workers = get_workers_list()
    if workers is not None:
        print(f"  Active Workers: {len(workers)}")
        for w in workers:
            name = w.get('id', 'unknown')
            modified = w.get('modified_on', '')
            if modified:
                try:
                    dt = datetime.fromisoformat(modified.replace('Z', '+00:00'))
                    modified = dt.strftime('%Y-%m-%d %H:%M')
                except (ValueError, AttributeError):
                    pass
            print(f"    - {name} (last modified: {modified})")
    else:
        print("  Could not fetch Workers list (may need Workers Scripts:Read permission)")

    print()

    # Workers analytics via GraphQL
    analytics = get_workers_analytics()
    if analytics and 'data' in analytics:
        accounts = analytics['data'].get('viewer', {}).get('accounts', [])
        if accounts:
            invocations = accounts[0].get('workersInvocationsAdaptive', [])

            # Aggregate by date
            daily = {}
            for inv in invocations:
                dt_str = inv['dimensions']['datetimeHour'][:10]
                reqs = inv['sum']['requests']
                errs = inv['sum']['errors']
                if dt_str not in daily:
                    daily[dt_str] = {'requests': 0, 'errors': 0}
                daily[dt_str]['requests'] += reqs
                daily[dt_str]['errors'] += errs

            # Aggregate by script
            by_script = {}
            for inv in invocations:
                script = inv['dimensions'].get('scriptName', 'unknown')
                reqs = inv['sum']['requests']
                if script not in by_script:
                    by_script[script] = 0
                by_script[script] += reqs

            limit = FREE_TIER['workers_requests_per_day']

            if daily:
                print("  Daily Requests (last 7 days):")
                print(f"  {'Date':<14} {'Requests':>10} {'Errors':>8} {'% of 100K':>10}")
                print(f"  {'─'*14} {'─'*10} {'─'*8} {'─'*10}")

                for date in sorted(daily.keys()):
                    d = daily[date]
                    pct = (d['requests'] / limit) * 100
                    warning = ' !!!' if pct >= 90 else ' !' if pct >= 70 else ''
                    print(f"  {date:<14} {d['requests']:>10,} {d['errors']:>8,} {pct:>9.1f}%{warning}")

                # Today's usage
                today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                today_reqs = daily.get(today, {}).get('requests', 0)
                today_pct = (today_reqs / limit) * 100
                print()
                print(f"  Today's usage: {progress_bar(today_pct)}")
                print(f"  ({format_num(today_reqs)} / {format_num(limit)} requests)")
            else:
                print("  No request data found for the last 7 days.")

            if by_script:
                print()
                print("  Requests by Script (7-day total):")
                for script, reqs in sorted(by_script.items(), key=lambda x: -x[1]):
                    print(f"    - {script}: {format_num(reqs)} requests")
        else:
            print("  No analytics data found.")
    else:
        errors = (analytics or {}).get('errors', [])
        if errors:
            print(f"  Analytics error: {errors[0].get('message', errors)}")
        else:
            print("  Could not fetch Workers analytics.")

    print()

    # ── 2. KV Storage ──────────────────────────────────────────────
    print("-" * 70)
    print("  2. KV Storage")
    print("-" * 70)

    kv_namespaces = get_kv_namespaces()
    if kv_namespaces is not None:
        print(f"  KV Namespaces: {len(kv_namespaces)}")
        for ns in kv_namespaces:
            title = ns.get('title', 'unknown')
            ns_id = ns.get('id', '')
            print(f"    - {title} ({ns_id[:12]}...)")
        print()
        print(f"  Free tier limits:")
        print(f"    Reads:   {format_num(FREE_TIER['kv_reads_per_day'])}/day")
        print(f"    Writes:  {format_num(FREE_TIER['kv_writes_per_day'])}/day")
        print(f"    Storage: {format_num(FREE_TIER['kv_storage_mb'])} MB")
        print()
        print(f"  Note: KV read/write counts require the Cloudflare Dashboard")
        print(f"  (GraphQL analytics or Dashboard > Workers & Pages > KV)")
    else:
        print("  Could not fetch KV namespaces (may need KV Storage:Read permission)")

    print()

    # ── 3. Pages ───────────────────────────────────────────────────
    print("-" * 70)
    print("  3. Cloudflare Pages")
    print("-" * 70)

    projects = get_pages_projects()
    if projects is not None:
        print(f"  Pages Projects: {len(projects)}")
        for proj in projects:
            name = proj.get('name', 'unknown')
            subdomain = proj.get('subdomain', '')
            created = proj.get('created_on', '')
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    created = dt.strftime('%Y-%m-%d')
                except (ValueError, AttributeError):
                    pass
            print(f"    - {name} ({subdomain}) created: {created}")

            # Get deployments for this project
            deployments = get_pages_deployments(name)
            if deployments:
                # Count deployments this month
                now = datetime.now(timezone.utc)
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                this_month = 0
                latest_deploy = None

                for dep in deployments:
                    dep_time = dep.get('created_on', '')
                    if dep_time:
                        try:
                            dt = datetime.fromisoformat(dep_time.replace('Z', '+00:00'))
                            if dt >= month_start:
                                this_month += 1
                            if latest_deploy is None:
                                latest_deploy = dt
                        except (ValueError, AttributeError):
                            pass

                deploy_limit = FREE_TIER['pages_deployments_per_month']
                deploy_pct = (this_month / deploy_limit) * 100

                print(f"      Deployments this month: {this_month} / {deploy_limit}")
                print(f"      Usage: {progress_bar(deploy_pct)}")
                if latest_deploy:
                    print(f"      Latest deploy: {latest_deploy.strftime('%Y-%m-%d %H:%M UTC')}")
    else:
        print("  Could not fetch Pages projects (may need Pages:Read permission)")

    print()

    # ── 4. Pages Functions ─────────────────────────────────────────
    print("-" * 70)
    print("  4. Pages Functions")
    print("-" * 70)

    pf_analytics = get_pages_functions_analytics()
    pf_daily = {}
    pf_by_script = {}

    if pf_analytics and 'data' in pf_analytics:
        accounts = pf_analytics['data'].get('viewer', {}).get('accounts', [])
        if accounts:
            # Try pagesFunctionsInvocationsAdaptiveGroups first
            invocations = accounts[0].get('pagesFunctionsInvocationsAdaptiveGroups', [])
            if invocations:
                for inv in invocations:
                    dt_str = inv['dimensions'].get('date', inv['dimensions'].get('datetimeHour', '')[:10])
                    reqs = inv['sum']['requests']
                    errs = inv['sum']['errors']
                    script = inv['dimensions'].get('scriptName', 'unknown')
                    if dt_str not in pf_daily:
                        pf_daily[dt_str] = {'requests': 0, 'errors': 0}
                    pf_daily[dt_str]['requests'] += reqs
                    pf_daily[dt_str]['errors'] += errs
                    pf_by_script[script] = pf_by_script.get(script, 0) + reqs
            else:
                # Fallback: workersInvocationsAdaptive (includes Pages Functions)
                invocations = accounts[0].get('workersInvocationsAdaptive', [])
                for inv in invocations:
                    dt_str = inv['dimensions']['datetimeHour'][:10]
                    reqs = inv['sum']['requests']
                    errs = inv['sum']['errors']
                    script = inv['dimensions'].get('scriptName', 'unknown')
                    if dt_str not in pf_daily:
                        pf_daily[dt_str] = {'requests': 0, 'errors': 0}
                    pf_daily[dt_str]['requests'] += reqs
                    pf_daily[dt_str]['errors'] += errs
                    pf_by_script[script] = pf_by_script.get(script, 0) + reqs

    limit = FREE_TIER['pages_functions_per_day']

    if pf_daily:
        print("  Daily Requests (last 7 days):")
        print(f"  {'Date':<14} {'Requests':>10} {'Errors':>8} {'% of 100K':>10}")
        print(f"  {'─'*14} {'─'*10} {'─'*8} {'─'*10}")

        for date in sorted(pf_daily.keys()):
            d = pf_daily[date]
            pct = (d['requests'] / limit) * 100
            warning = ' !!!' if pct >= 90 else ' !' if pct >= 70 else ''
            print(f"  {date:<14} {d['requests']:>10,} {d['errors']:>8,} {pct:>9.1f}%{warning}")

        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        today_reqs = pf_daily.get(today, {}).get('requests', 0)
        today_pct = (today_reqs / limit) * 100
        print()
        print(f"  Today's usage: {progress_bar(today_pct)}")
        print(f"  ({format_num(today_reqs)} / {format_num(limit)} invocations)")

        if pf_by_script:
            print()
            print("  Requests by Script (7-day total):")
            for script, reqs in sorted(pf_by_script.items(), key=lambda x: -x[1]):
                print(f"    - {script}: {format_num(reqs)} requests")
    else:
        print(f"  No Pages Functions request data found.")
        print(f"  Free tier limit: {format_num(limit)} invocations/day")

    print()

    # ── 5. Summary ─────────────────────────────────────────────────
    print("-" * 70)
    print("  5. Summary")
    print("-" * 70)

    checks = []

    # Workers requests check
    if analytics and 'data' in analytics:
        accounts = analytics['data'].get('viewer', {}).get('accounts', [])
        if accounts:
            invocations = accounts[0].get('workersInvocationsAdaptive', [])
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            today_reqs = sum(
                inv['sum']['requests'] for inv in invocations
                if inv['dimensions']['datetimeHour'][:10] == today
            )
            limit = FREE_TIER['workers_requests_per_day']
            pct = (today_reqs / limit) * 100
            if pct < 70:
                checks.append(('Workers Requests', 'OK', f'{format_num(today_reqs)}/{format_num(limit)}/day'))
            elif pct < 90:
                checks.append(('Workers Requests', 'WARNING', f'{format_num(today_reqs)}/{format_num(limit)}/day'))
            else:
                checks.append(('Workers Requests', 'CRITICAL', f'{format_num(today_reqs)}/{format_num(limit)}/day'))

    # Pages Functions requests check
    if pf_daily:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        today_pf_reqs = pf_daily.get(today, {}).get('requests', 0)
        pf_limit = FREE_TIER['pages_functions_per_day']
        pct = (today_pf_reqs / pf_limit) * 100
        if pct < 70:
            checks.append(('Pages Functions', 'OK', f'{format_num(today_pf_reqs)}/{format_num(pf_limit)}/day'))
        elif pct < 90:
            checks.append(('Pages Functions', 'WARNING', f'{format_num(today_pf_reqs)}/{format_num(pf_limit)}/day'))
        else:
            checks.append(('Pages Functions', 'CRITICAL', f'{format_num(today_pf_reqs)}/{format_num(pf_limit)}/day'))

    # Pages deployments check (feedown only to avoid too many API calls)
    if projects:
        feedown_proj = next((p for p in projects if p.get('name') == 'feedown'), None)
        if feedown_proj:
            deployments = get_pages_deployments('feedown')
            if deployments:
                now = datetime.now(timezone.utc)
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                this_month = sum(
                    1 for d in deployments
                    if d.get('created_on') and
                    datetime.fromisoformat(d['created_on'].replace('Z', '+00:00')) >= month_start
                )
                limit = FREE_TIER['pages_deployments_per_month']
                pct = (this_month / limit) * 100
                if pct < 70:
                    checks.append(('Pages Deploys (feedown)', 'OK', f'{this_month}/{limit}/month'))
                elif pct < 90:
                    checks.append(('Pages Deploys (feedown)', 'WARNING', f'{this_month}/{limit}/month'))
                else:
                    checks.append(('Pages Deploys (feedown)', 'CRITICAL', f'{this_month}/{limit}/month'))

    if not checks:
        checks.append(('Overall', 'OK', 'Could not verify all metrics (check permissions)'))

    for name, status, detail in checks:
        icon = {'OK': '[OK]', 'WARNING': '[!!]', 'CRITICAL': '[XX]'}[status]
        print(f"  {icon} {name:<30} {detail}")

    print()

    statuses = [s for _, s, _ in checks]
    if 'CRITICAL' in statuses:
        print("  >>> VERDICT: Approaching free tier limits! Monitor closely. <<<")
    elif 'WARNING' in statuses:
        print("  >>> VERDICT: Within limits, but monitor growth. <<<")
    else:
        print("  >>> VERDICT: Comfortably within free tier limits. <<<")

    print()
    print("=" * 70)


if __name__ == '__main__':
    main()
