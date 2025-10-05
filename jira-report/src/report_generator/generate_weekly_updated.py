#!/usr/bin/env python3
from __future__ import annotations

import sys

from report_generator import (
    DEFAULT_FIELDS,
    make_session,
    build_auth,
    jql_for_updated,
    search_jira_auto_version,
    to_html,
    write_html,
    write_json,
    set_vars
)


def main():
    (args, base_url, basic_email, basic_token, project, cfg) = (
        set_vars(config_file="jira-report/configs/weekly_updated.json")
    )

    outputs = cfg.get("outputs", {})
    out_json = outputs.get("json")
    out_html = outputs.get("html")
    days = cfg.get("days", 7)
    issue_types = cfg.get("issue_types", ["Bug", "Story", "Task"])
    api_version = cfg.get("api_version", "auto").strip()
    fields = cfg.get("fields", DEFAULT_FIELDS)
    page_size = cfg.get("page_size", 100)
    title = f"{project} — Bugs/Stories/Tasks updated in the last {days} days"
    session = make_session(verify_ssl=True)
    headers = build_auth(session, bearer_token=basic_token, basic_email=basic_email, basic_api_token=basic_token)

    jql = jql_for_updated(project, issue_types, days)

    try:
        issues = search_jira_auto_version(
            base_url, api_version, jql, fields, session, headers, page_size, debug=True
        )
    except Exception as e:
        print(f"❌ Jira query failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Write outputs
    write_json(out_json, {"count": len(issues), "jql": jql, "issues": issues})
    html = to_html(issues, base_url, title)
    write_html(out_html, html)

    print(f"✅ Done. Fetched {len(issues)} issues.")
    print(f"   Saved JSON -> {out_json}")
    print(f"   Saved HTML -> {out_html}")
    print(f"   JQL used   -> {jql}")


if __name__ == "__main__":
    main()
