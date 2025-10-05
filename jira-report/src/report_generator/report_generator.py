#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, quote_plus

import requests
from requests.adapters import HTTPAdapter, Retry
from requests.auth import HTTPBasicAuth

DEFAULT_FIELDS = [
    "key", "summary", "issuetype", "status", "assignee", "reporter",
    "priority", "updated", "created", "resolutiondate", "components", "fixVersions",
]


def make_session(timeout: int = 30, verify_ssl: bool = True) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.verify = verify_ssl
    s.request_timeout = timeout
    s.headers.update({"User-Agent": "jira-weekly-updated/1.0 (+requests)"})
    return s


def build_auth(
        session: requests.Session,
        bearer_token: Optional[str] = None,
        basic_email: Optional[str] = None,
        basic_api_token: Optional[str] = None,
) -> Dict[str, str]:
    """
    Builds auth headers. Prefer Bearer (PAT) if provided, else Basic (email + API token).
    """
    headers: Dict[str, str] = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token.strip()}"
    elif basic_email and basic_api_token:
        session.auth = HTTPBasicAuth(basic_email.strip(), basic_api_token.strip())
    return headers


def jql_for_updated(project: str, issue_types: List[str], days: int) -> str:
    quoted_types = ",".join(f'"{t}"' for t in issue_types)
    return f'project = "{project}" AND issuetype in ({quoted_types}) AND updated >= -{days}d ORDER BY updated DESC'


def _search_once(
        base_url: str,
        api_version: str,
        jql: str,
        fields: List[str],
        session: requests.Session,
        headers: Dict[str, str],
        page_size: int,
        debug: bool = False,
) -> List[Dict[str, Any]]:
    all_issues: List[Dict[str, Any]] = []
    start_at = 0
    endpoint = f"{base_url.rstrip('/')}/rest/api/{api_version}/search"
    fields_param = ",".join(fields)

    while True:
        params = {"jql": jql, "startAt": start_at, "maxResults": page_size, "fields": fields_param}
        url = f"{endpoint}?{urlencode(params, quote_via=quote_plus)}"
        r = session.get(url, headers=headers, timeout=getattr(session, "request_timeout", 30), allow_redirects=True)

        ct = r.headers.get("Content-Type", "")
        if debug:
            hist = " -> ".join(resp.headers.get("Location", resp.url) for resp in r.history) if r.history else ""
            print(f"[DEBUG] GET {url}")
            print(
                f"[DEBUG] Status={r.status_code} Content-Type={ct!r} Redirects={len(r.history)} {'(' + hist + ')' if hist else ''}")

        if r.status_code >= 400:
            snippet = (r.text or "")[:600]
            raise RuntimeError(f"HTTP {r.status_code} from {url}\nHeaders: {dict(r.headers)}\nBody: {snippet}")

        if "application/json" not in ct.lower():
            snippet = (r.text or "")[:600]
            login_hint = "\n(Detected redirect chain — likely SSO/login; ensure API token/PAT auth is used.)" if r.history else ""
            raise RuntimeError(
                f"Expected JSON but got Content-Type={ct!r} at {r.url}{login_hint}\nFirst 600 bytes:\n{snippet}"
            )

        try:
            data = r.json()
        except Exception as e:
            snippet = (r.text or "")[:600]
            raise RuntimeError(f"Failed to decode JSON at {r.url}: {e}\nFirst 600 bytes:\n{snippet}")

        issues = data.get("issues", [])
        all_issues.extend(issues)

        total = int(data.get("total", len(all_issues)))
        start_at = int(data.get("startAt", start_at))
        max_results = int(data.get("maxResults", page_size))

        if debug:
            print(f"[DEBUG] Page: startAt={start_at} maxResults={max_results} pageCount={len(issues)} total={total}")

        if start_at + max_results >= total or not issues:
            break
        start_at += max_results

    return all_issues


def search_jira_auto_version(
        base_url: str,
        api_version: str,
        jql: str,
        fields: List[str],
        session: requests.Session,
        headers: Dict[str, str],
        page_size: int,
        debug: bool = False,
) -> List[Dict[str, Any]]:
    versions = [api_version] if api_version.lower() != "auto" else ["3", "2"]
    last_err: Optional[Exception] = None
    for v in versions:
        try:
            if debug:
                print(f"[DEBUG] Trying Jira REST API v{v}")
            return _search_once(base_url, v, jql, fields, session, headers, page_size, debug=debug)
        except Exception as e:
            last_err = e
            if debug:
                print(f"[DEBUG] v{v} failed: {e}")
    raise last_err if last_err else RuntimeError("Unknown error (no versions tried)")


def to_html(issues: List[Dict[str, Any]], base_url: str, title: str) -> str:
    def safe(d: Any, *path, default=""):
        cur = d
        for k in path:
            if cur is None:
                return default
            cur = cur.get(k) if isinstance(cur, dict) else None
        return cur if cur is not None else default

    rows: List[str] = []
    rows.append(
        "<tr>"
        "<th>Key</th><th>Type</th><th>Summary</th><th>Status</th>"
        "<th>Assignee</th><th>Updated</th><th>Components</th>"
        "</tr>"
    )
    for it in issues:
        key = it.get("key", "")
        f = it.get("fields", {})
        link = f'{base_url.rstrip("/")}/browse/{key}' if key else "#"
        issuetype = safe(f, "issuetype", "name")
        status = safe(f, "status", "name")
        assignee = safe(f, "assignee", "displayName")
        summary = f.get("summary", "")
        updated = f.get("updated", "")
        comps = ", ".join([c.get("name", "") for c in (f.get("components") or [])])

        rows.append(
            "<tr>"
            f'<td><a href="{link}">{key}</a></td>'
            f"<td>{issuetype}</td>"
            f"<td>{summary}</td>"
            f"<td>{status}</td>"
            f"<td>{assignee}</td>"
            f"<td>{updated}</td>"
            f"<td>{comps}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; }}
    h1 {{ margin-bottom: 8px; }}
    .meta {{ color: #555; margin-bottom: 16px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f7f7f7; text-align: left; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    a {{ text-decoration: none; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <table>
    {''.join(rows)}
  </table>
</body>
</html>"""


def write_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_html(path: str, html: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


# ----- Sprint helpers (Agile API) -----

def get_board_id(
        base_url: str,
        session: requests.Session,
        headers: Dict[str, str],
        project_key_or_id: Optional[str] = None,
        board_id: Optional[int] = None,
        debug: bool = False,
) -> Dict[str, Any]:
    """Return {'id': int, 'name': str} for the board.
    If board_id is given, verify it. Otherwise, find by project using Agile API.
    """
    if board_id is not None:
        url = f"{base_url.rstrip('/')}/rest/agile/1.0/board/{board_id}"
        session.verify = True
        session.trust_env = False
        r = session.get(url, timeout=getattr(session, "request_timeout", 30), allow_redirects=True, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"Cannot load board {board_id}: HTTP {r.status_code} {r.text[:300]}")
        data = r.json()
        return {"id": data.get("id", board_id), "name": data.get("name", f"Board {board_id}")}
    if not project_key_or_id:
        raise ValueError("project_key_or_id is required when board_id is not provided")
    url = f"{base_url.rstrip('/')}/rest/agile/1.0/board?projectKeyOrId={project_key_or_id}"
    r = session.get(url, headers=headers, timeout=getattr(session, "request_timeout", 30))
    if r.status_code >= 400:
        raise RuntimeError(f"Cannot list boards for project {project_key_or_id}: HTTP {r.status_code} {r.text[:300]}")
    data = r.json()
    boards = data.get("values", [])
    if not boards:
        raise RuntimeError(f"No boards found for project {project_key_or_id}")
    # Heuristic: prefer Scrum boards
    boards_sorted = sorted(boards, key=lambda b: (b.get("type") != "scrum", b.get("name", "")))
    b0 = boards_sorted[0]
    return {"id": b0.get("id"), "name": b0.get("name")}


def to_html_sprint(report: Dict[str, Any], base_url: str) -> str:
    meta = report.get("meta", {})
    aggr = report.get("aggregates", {})
    issues = report.get("issues", [])

    def tr(k, v):
        return f"<tr><td>{k}</td><td>{v}</td></tr>"

    # Aggregates tables
    def table_dict(d: Dict[str, Any], title: str) -> str:
        rows = ["<tr><th>Name</th><th>Count</th><th>Story Points</th></tr>"]
        for name, vals in sorted(d.items(), key=lambda x: x[0].lower()):
            rows.append(f"<tr><td>{name}</td><td>{vals.get('count', 0)}</td><td>{vals.get('sp', 0.0):.2f}</td></tr>")
        return f"<h3>{title}</h3><table>{''.join(rows)}</table>"

    # Issues table
    issue_rows = ["<tr><th>Key</th><th>Type</th><th>Summary</th><th>Status</th><th>Assignee</th></tr>"]
    for it in issues:
        key = it.get("key", "")
        f = it.get("fields", {})
        link = f"{base_url.rstrip('/')}/browse/{key}" if key else "#"
        itype = (f.get("issuetype") or {}).get("name") or ""
        status = (f.get("status") or {}).get("name") or ""
        assignee = (f.get("assignee") or {}).get("displayName") or ""
        summary = f.get("summary", "")
        issue_rows.append(
            f"<tr><td><a href='{link}'>{key}</a></td><td>{itype}</td><td>{summary}</td><td>{status}</td><td>{assignee}</td></tr>"
        )

    html = f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <title>Sprint Status — {meta.get('sprint', {}).get('name', '')}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .meta, .section {{ margin-bottom: 18px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f7f7f7; text-align: left; }}
    tr:nth-child(even) {{ background: #fafafa; }}
  </style>
</head>
<body>
  <h1>Sprint Status</h1>
  <div class='meta'>
    <table>
      {tr('Project', meta.get('project', ''))}
      {tr('Board', f"{meta.get('board', {}).get('name', '')} (#{meta.get('board', {}).get('id', '')})")}
      {tr('Sprint', f"{meta.get('sprint', {}).get('name', '')} (#{meta.get('sprint', {}).get('id', '')})")}
      {tr('Dates', f"{meta.get('sprint', {}).get('startDate', '')} → {meta.get('sprint', {}).get('endDate', '')}")}
      {tr('Issues', meta.get('counts', {}).get('issues', 0))}
      {tr('Story Points (total)', f"{meta.get('story_points_total', 0.0):.2f}")}
    </table>
  </div>

  <div class='section'>
    {table_dict(aggr.get('by_status', {}), 'By Status')}
  </div>
  <div class='section'>
    {table_dict(aggr.get('by_category', {}), 'By Category')}
  </div>
  <div class='section'>
    {table_dict(aggr.get('by_type', {}), 'By Issue Type')}
  </div>

  <h2>Issues</h2>
  <table>
    {''.join(issue_rows)}
  </table>
</body>
</html>"""
    return html


def load_config(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def set_vars(config_file):
    parser = argparse.ArgumentParser(description="Generate weekly 'updated in last N days' report from Jira.")
    parser.add_argument("--config", help="Path to JSON config (e.g., configs/weekly_updated.json)")
    parser.set_defaults(verify_ssl=True)
    args = parser.parse_args()
    cfg = load_config(config_file)
    project = cfg.get("project", "BUILD")
    basic_email = os.getenv("JIRA_EMAIL", "").strip()
    basic_token = os.getenv("JIRA_API_TOKEN", "").strip()
    base_url = os.getenv("JIRA_BASE_URL", "").strip()
    if not (base_url and basic_email and basic_token):
        print("ERROR: Set JIRA env ")
        sys.exit(2)
    return (args, base_url, basic_email, basic_token, project, cfg)
