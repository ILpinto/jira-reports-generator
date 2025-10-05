#!/usr/bin/env python3
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

import requests

from report_generator import (
    DEFAULT_FIELDS,
    make_session,
    build_auth,
    to_html_sprint,
    write_html,
    write_json,
    set_vars,
    get_board_id,
    search_jira_auto_version
)


def get_active_sprint(
        base_url: str,
        board_id: int,
        session: requests.Session,
        headers: Dict[str, str],
) -> Dict[str, Any]:
    """Return {'id': int, 'name': str, 'startDate': ..., 'endDate': ...} for the active sprint on a board."""
    url = f"{base_url.rstrip('/')}/rest/agile/1.0/board/{board_id}/sprint?state=active"
    r = session.get(url, headers=headers, timeout=getattr(session, "request_timeout", 30))
    if r.status_code >= 400:
        raise RuntimeError(f"Cannot list active sprints for board {board_id}: HTTP {r.status_code} {r.text[:300]}")
    data = r.json()
    sprints = data.get("values", [])
    if not sprints:
        raise RuntimeError(f"No active sprint found on board {board_id}")
    return sprints[0]


def fetch_sprint_issues(
        base_url: str,
        sprint_id: int,
        session: requests.Session,
        headers: Dict[str, str],
        fields: List[str],
        page_size: int = 100,
        api_version: str = "auto",
        debug: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch issues for a sprint using the Search API (more flexible fields control)."""
    jql = f"sprint = {sprint_id} ORDER BY Rank ASC"
    return search_jira_auto_version(
        base_url, api_version, jql, fields, session, headers, page_size, debug=debug
    )


def _get_story_points(fields_obj: Dict[str, Any], sp_field_key: Optional[str]) -> float:
    # Try configured field key first, then common defaults
    keys = [sp_field_key] if sp_field_key else []
    keys += "Custom field (Story Points)"
    for k in keys:
        if not k:
            continue
        if k in fields_obj and fields_obj[k] is not None:
            try:
                return float(fields_obj[k])
            except Exception:
                try:
                    return float(str(fields_obj[k]))
                except Exception:
                    continue
    return 0.0


def sprint_status_report(
        base_url: str,
        project: str,
        session: requests.Session,
        headers: Dict[str, str],
        *,
        board_id: Optional[int] = None,
        use_current_sprint: bool = True,
        sprint_id: Optional[int] = None,
        api_version: str = "auto",
        page_size: int = 100,
        fields: Optional[List[str]] = None,
        story_points_field: Optional[str] = None,
        debug: bool = True,
) -> Dict[str, Any]:
    """Build a sprint status report, returning a dict with metadata, aggregates, and issues."""
    if fields is None:
        fields = [
            "key", "summary", "issuetype", "status", "assignee",
            "priority", "updated", "created", "resolutiondate",
            "components"
        ]
        # Include the configured SP field if provided
        if story_points_field:
            fields.append(story_points_field)

    # Determine board
    binfo = get_board_id(base_url, session, headers, project_key_or_id=project, board_id=board_id, debug=debug)
    board_id_resolved = binfo["id"]
    board_name = binfo.get("name", str(board_id_resolved))

    # Determine sprint
    sinfo = None
    if sprint_id is not None:
        # Optionally verify sprint
        sinfo = {"id": sprint_id, "name": f"Sprint {sprint_id}"}
    elif use_current_sprint:
        sinfo = get_active_sprint(base_url, board_id_resolved, session, headers)
    else:
        raise ValueError("Either sprint_id must be provided or use_current_sprint=True")

    sid = sinfo.get("id")
    sname = sinfo.get("name", f"Sprint {sid}")
    sstart = sinfo.get("startDate")
    send = sinfo.get("endDate")

    # Fetch issues
    issues = fetch_sprint_issues(
        base_url, sid, session, headers, fields=fields, page_size=page_size, api_version=api_version, debug=debug
    )

    # Aggregates
    by_status = {}
    by_category = {}
    by_type = {}
    total_sp = 0.0

    for it in issues:
        f = it.get("fields", {})
        status = (f.get("status") or {}).get("name") or "Unknown"
        cat = ((f.get("status") or {}).get("statusCategory") or {}).get("name") or "Unknown"
        itype = (f.get("issuetype") or {}).get("name") or "Unknown"
        sp = _get_story_points(f, story_points_field)

        by_status[status] = by_status.get(status, {"count": 0, "sp": 0.0})
        by_status[status]["count"] += 1
        by_status[status]["sp"] += sp

        by_category[cat] = by_category.get(cat, {"count": 0, "sp": 0.0})
        by_category[cat]["count"] += 1
        by_category[cat]["sp"] += sp

        by_type[itype] = by_type.get(itype, {"count": 0, "sp": 0.0})
        by_type[itype]["count"] += 1
        by_type[itype]["sp"] += sp

        total_sp += sp

    meta = {
        "project": project,
        "board": {"id": board_id_resolved, "name": board_name},
        "sprint": {"id": sid, "name": sname, "startDate": sstart, "endDate": send},
        "counts": {"issues": len(issues)},
        "story_points_total": total_sp,
    }

    return {
        "meta": meta,
        "aggregates": {
            "by_status": by_status,
            "by_category": by_category,
            "by_type": by_type,
        },
        "issues": issues,
    }


def main():
    (args, base_url, basic_email, basic_token, project, cfg) = (
        set_vars(config_file="jira-report/configs/sprint_status.json")
    )

    # Outputs
    outputs = cfg.get("outputs", {})
    out_json = outputs.get("json")
    out_html = outputs.get("html")
    fields = cfg.get("fields", DEFAULT_FIELDS)

    sprint_id = cfg.get("sprint_id")
    use_current = cfg.get("use_current_sprint", True) or (sprint_id is None)
    api_version = cfg.get("api_version", "auto").strip()
    page_size = cfg.get("page_size", 100)
    story_points_field = cfg.get("story_points_field")
    board_id = cfg.get("board_id")
    session = make_session(verify_ssl=args.verify_ssl)
    headers = build_auth(session, bearer_token=basic_token, basic_email=basic_email, basic_api_token=basic_token)
    # Determine board
    binfo = get_board_id(base_url, session, headers, project_key_or_id=project, board_id=board_id, debug=True)
    board_id_resolved = binfo["id"]
    board_name = binfo.get("name", str(board_id_resolved))

    try:
        report = sprint_status_report(
            base_url,
            project,
            session,
            headers,
            board_id=board_id,
            use_current_sprint=use_current,
            sprint_id=sprint_id,
            api_version=api_version,
            page_size=page_size,
            fields=fields,
            story_points_field=story_points_field,
            debug=True,
        )
    except Exception as e:
        print(f"❌ Sprint report failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Write outputs
    write_json(out_json, report)
    html = to_html_sprint(report, base_url)
    write_html(out_html, html)

    print("✅ Sprint status report generated.")
    print(f"   Saved JSON -> {out_json}")
    print(f"   Saved HTML -> {out_html}")
    meta = report.get("meta", {})
    print(f"   Sprint     -> {meta.get('sprint', {}).get('name', '')} (#{meta.get('sprint', {}).get('id', '')})")
    print(f"   Board      -> {meta.get('board', {}).get('name', '')} (#{meta.get('board', {}).get('id', '')})")
    print(f"   Issues     -> {meta.get('counts', {}).get('issues', 0)}")
    print(f"   Story Pts  -> {meta.get('story_points_total', 0.0):.2f}")


if __name__ == "__main__":
    main()
