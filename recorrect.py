"""CLI for the per-project correction dictionary (Phase 9.6b).

Batch-applies .arkiv/corrections.json's post-rules to the active project's
stored transcripts — the cheap, audio-free path to fix the whole backlog's
search recall. Preview-first and reversible (RP-4).

  python recorrect.py --dry-run            # preview hits, write nothing (default)
  python recorrect.py --apply              # apply + write a backup
  python recorrect.py --apply --rebuild    # apply, then rebuild the vector index
  python recorrect.py --list-backups
  python recorrect.py --revert [NAME]      # restore latest (or named) backup

Operates on the ACTIVE project (ARKIV_PROJECT_ROOT / config.DB_PATH). Point
those at the target project's .arkiv before running.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import config
import corrections


def _print_scan(scan: dict) -> None:
    print("📋 Dry-run — 預覽（未寫入）")
    print("  規則命中：")
    for r in scan["rules"]:
        mark = "•" if r["hits"] else "·"
        print("    {0} {1} → {2}  [{3}]  命中 {4}".format(
            mark, r["from"], r["to"] or "(刪除)", r["scope"], r["hits"]))
    print("  受影響素材：{0} 筆，總命中 {1}".format(
        scan["media_affected"], scan["total_hits"]))
    for m in scan["affected"][:20]:
        parts = ", ".join("{0}→{1}×{2}".format(s["from"], s["to"], s["count"])
                          for s in m["rules"])
        print("    #{0} {1}  ({2})".format(m["id"], m["filename"], parts))
    if scan["media_affected"] > 20:
        print("    … 另 {0} 筆".format(scan["media_affected"] - 20))


def main() -> int:
    parser = argparse.ArgumentParser(description="arkiv correction dictionary — batch recorrect")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="預覽命中，不寫入（預設）")
    g.add_argument("--apply", action="store_true", help="套用校正（寫 backup 後改 transcript + segments）")
    g.add_argument("--list-backups", action="store_true", help="列出可還原的 backup")
    g.add_argument("--revert", nargs="?", const="", metavar="NAME", help="還原最新（或指定）backup")
    parser.add_argument("--rebuild", action="store_true", help="套用後重建向量索引（搭 --apply）")
    args = parser.parse_args()

    rules = corrections.load_rules()
    print("專案：{0}".format(config.PROJECT_ROOT))
    print("字典：{0}（{1} 條規則）".format(corrections.corrections_path(), len(rules)))

    if args.list_backups:
        for name in corrections.list_backups():
            print("  {0}".format(name))
        return 0

    if args.revert is not None:
        result = corrections.revert(args.revert or None)
        if result.get("error"):
            print("⚠️  還原失敗：{0}".format(result["error"]))
            return 1
        print("↩️  已還原 {0} 筆（backup {1}）".format(result["restored"], result["backup"]))
        return 0

    if not rules:
        print("⚠️  字典為空，無事可做。")
        return 0

    if args.apply:
        result = corrections.apply()
        print("✅ 已套用 {0} 條規則：更新 {1} 筆素材、{2} 處替換。".format(
            result["rule_count"], result["media_updated"], result["total_hits"]))
        if result["backup"]:
            print("   backup：{0}（--revert 可還原）".format(result["backup"]))
        if args.rebuild and result["media_updated"]:
            print("🔁 重建向量索引…")
            subprocess.run([sys.executable, str(Path(__file__).parent / "embed.py"), "--rebuild"], check=False)
        return 0

    # default: dry-run
    _print_scan(corrections.scan(rules))
    return 0


if __name__ == "__main__":
    sys.exit(main())
