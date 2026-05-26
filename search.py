from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

import federation
import projects


def _split_csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    items = []
    for part in value.split(","):
        part = part.strip()
        if part:
            items.append(part)
    return items or None


def _table_lines(items: List[Dict[str, Any]]) -> str:
    rows = [["score", "project", "media_id", "filename", "path", "excerpt"]]
    for item in items:
        rows.append([
            "{0:.4f}".format(float(item.get("score", 0))),
            item.get("project_name", ""),
            str(item.get("media_id", "")),
            item.get("filename", ""),
            item.get("absolute_path") or item.get("path") or "",
            (item.get("excerpt") or "")[:80],
        ])
    widths = [max(len(str(row[idx])) for row in rows) for idx in range(len(rows[0]))]
    lines = []
    for row_index, row in enumerate(rows):
        lines.append("  ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(row)))
        if row_index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def _json_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _all_projects_exit_code(payload: Dict[str, Any]) -> int:
    if payload.get("errors"):
        stages = {error.get("stage") for error in payload["errors"] if isinstance(error, dict)}
        if payload.get("items"):
            return 0
        if stages & {"path_not_found", "db_missing", "chroma_missing", "nas_unmounted"}:
            return 4
        if payload.get("projects_queried", 0) and payload.get("projects_failed", 0) >= payload.get("projects_queried", 0):
            return 2
        if any(error.get("error") == "no matching projects" for error in payload["errors"] if isinstance(error, dict)):
            return 1
    if payload.get("items"):
        return 0
    return 1


def _single_project_payload(query: str, limit: int) -> Dict[str, Any]:
    import vectordb

    items = vectordb.search(query, n_results=limit)
    return {
        "query": query,
        "total_results": len(items),
        "projects_queried": 1,
        "projects_failed": 0,
        "items": items,
        "errors": [],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="arkiv search")
    parser.add_argument("--query", required=True)
    parser.add_argument("--all-projects", action="store_true")
    parser.add_argument("--projects")
    parser.add_argument("--tag")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--per-project-limit", type=int, default=20)
    parser.add_argument("--format", choices=["table", "json", "jsonl"], default="table")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--no-fallback-sql", action="store_true")
    args = parser.parse_args(argv)

    project_names = _split_csv(args.projects)
    if args.all_projects or project_names or args.tag:
        try:
            payload = federation.search_all_projects(
                args.query,
                limit=args.limit,
                per_project_limit=args.per_project_limit,
                project_names=project_names,
                tag=args.tag,
                timeout=args.timeout,
                fallback_sql=not args.no_fallback_sql,
            )
        except projects.RegistryError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        if args.format == "json":
            print(_json_payload(payload))
        elif args.format == "jsonl":
            for item in payload["items"]:
                print(json.dumps(item, ensure_ascii=False))
        else:
            print(_table_lines(payload["items"]))
        return _all_projects_exit_code(payload)

    payload = _single_project_payload(args.query, args.limit)
    if args.format == "json":
        print(_json_payload(payload))
    elif args.format == "jsonl":
        for item in payload["items"]:
            print(json.dumps(item, ensure_ascii=False))
    else:
        print(_table_lines(payload["items"]))
    return 0 if payload["items"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
