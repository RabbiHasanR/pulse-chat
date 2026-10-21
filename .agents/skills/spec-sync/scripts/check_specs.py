#!/usr/bin/env python3
"""Validate Pulse Chat specs, ADRs, skills and doc links.

Standard library only. Run from anywhere inside the repo:

    python3 .agents/skills/spec-sync/scripts/check_specs.py

Exit code 0 = all checks passed, 1 = errors found.
Warnings (e.g. stale ``last_verified``) never fail the run.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
VALID_STATUSES = {"draft", "approved", "implemented", "deprecated"}
VALID_AREAS = {"auth": "AUTH", "users": "USR", "chats": "CHAT", "realtime": "RT", "media": "MEDIA", "ops": "OPS"}
REQUIRED_KEYS = ("id", "title", "status", "area", "code", "last_verified")
SPEC_ID_RE = re.compile(r"^(AUTH|USR|CHAT|RT|MEDIA|OPS)-\d{3}$")
AC_RE = re.compile(r"^\s*-\s+(?:~~)?\*\*AC(\d+)\*\*", re.MULTILINE)
BUG_REF_RE = re.compile(r"\bBUG-\d{3}\b")
BUG_DEF_RE = re.compile(r"^## (BUG-\d{3})\b", re.MULTILINE)
ADR_FILE_RE = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
STALE_DAYS = 120
SKIP_FILES = {"README.md", "_TEMPLATE.md"}


def find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


ROOT = find_repo_root()
DOCS = ROOT / "docs"
SPECS = DOCS / "specs"
ADRS = DOCS / "adr"
SKILLS = ROOT / ".agents" / "skills"

errors: list[str] = []
warnings: list[str] = []


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def err(path: Path, msg: str) -> None:
    errors.append(f"{rel(path)}: {msg}")


def warn(path: Path, msg: str) -> None:
    warnings.append(f"{rel(path)}: {msg}")


# --------------------------------------------------------------------------- #
# Minimal YAML-frontmatter parser (scalars, inline lists, block lists)
# --------------------------------------------------------------------------- #
def parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    data: dict = {}
    current_key = None
    for raw in text[4:end].splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(line.lstrip()[2:].strip().strip("'\""))
            continue
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            current_key = key
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                data[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
            elif value == "":
                data[key] = []
            else:
                data[key] = value.strip("'\"")
    return data


def strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks and inline code so links/IDs inside them are ignored."""
    text = re.sub(r"(`{3,}|~{3,}).*?\1", "", text, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", text)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_specs() -> dict[str, dict]:
    specs: dict[str, dict] = {}
    if not SPECS.exists():
        err(SPECS, "specs directory missing")
        return specs

    today = dt.date.today()
    for path in sorted(SPECS.rglob("*.md")):
        if path.name in SKIP_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if fm is None:
            err(path, "missing or malformed YAML frontmatter")
            continue

        for key in REQUIRED_KEYS:
            if key not in fm or fm[key] in ("", []):
                err(path, f"frontmatter missing required key '{key}'")

        sid = fm.get("id", "")
        if not SPEC_ID_RE.match(str(sid)):
            err(path, f"invalid spec id '{sid}'")
        if sid and not path.name.startswith(f"{sid}-"):
            err(path, f"file name must start with '{sid}-'")
        if sid in specs:
            err(path, f"duplicate spec id {sid} (also in {specs[sid]['path']})")

        area = fm.get("area", "")
        if area not in VALID_AREAS:
            err(path, f"invalid area '{area}' (allowed: {', '.join(VALID_AREAS)})")
        else:
            if path.parent.name != area:
                err(path, f"spec with area '{area}' must live in docs/specs/{area}/")
            if sid and not str(sid).startswith(VALID_AREAS[area] + "-"):
                err(path, f"id prefix must be {VALID_AREAS[area]} for area '{area}'")

        status = fm.get("status", "")
        if status not in VALID_STATUSES:
            err(path, f"invalid status '{status}'")

        code = fm.get("code", [])
        if isinstance(code, str):
            code = [code]
        for entry in code:
            target = re.split(r"::|#", entry, maxsplit=1)[0]
            if not (ROOT / target).exists():
                err(path, f"code path does not exist: {entry}")

        acs = [int(n) for n in AC_RE.findall(text)]
        if not acs and status != "deprecated":
            err(path, "no acceptance criteria found (expected '- **AC1** ...')")
        dupes = {n for n in acs if acs.count(n) > 1}
        if dupes:
            err(path, f"duplicate AC numbers: {sorted(dupes)}")

        lv = fm.get("last_verified", "")
        try:
            age = (today - dt.date.fromisoformat(str(lv))).days
            if age > STALE_DAYS and status == "implemented":
                warn(path, f"last_verified is {age} days old — run spec-sync")
        except ValueError:
            err(path, f"last_verified '{lv}' is not an ISO date (YYYY-MM-DD)")

        specs[str(sid)] = {"path": rel(path), "status": status, "text": text}
    return specs


def check_spec_index(specs: dict[str, dict]) -> None:
    index = SPECS / "README.md"
    if not index.exists():
        err(index, "spec index missing")
        return
    text = index.read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*\[?((?:AUTH|USR|CHAT|RT|MEDIA|OPS)-\d{3})\]?(?:\([^)]*\))?\s*\|[^|]*\|\s*`?(\w+)`?\s*\|", text, re.MULTILINE)
    listed = {sid: st for sid, st in rows}
    for sid, info in specs.items():
        if sid not in listed:
            err(index, f"{sid} ({info['path']}) is not listed in the index")
        elif listed[sid] != info["status"]:
            err(index, f"{sid} status in index is '{listed[sid]}' but spec says '{info['status']}'")
    for sid in listed:
        if sid not in specs:
            err(index, f"index lists {sid} but no spec file exists")


def check_bug_refs(specs: dict[str, dict]) -> None:
    ki = DOCS / "KNOWN_ISSUES.md"
    if not ki.exists():
        return
    defined = set(BUG_DEF_RE.findall(ki.read_text(encoding="utf-8")))
    for sid, info in specs.items():
        for ref in set(BUG_REF_RE.findall(info["text"])):
            if ref not in defined:
                err(ROOT / info["path"], f"references {ref} which is not defined in docs/KNOWN_ISSUES.md")


def check_adrs() -> None:
    if not ADRS.exists():
        return
    seen: dict[str, Path] = {}
    index_text = (ADRS / "README.md").read_text(encoding="utf-8") if (ADRS / "README.md").exists() else ""
    for path in sorted(ADRS.glob("*.md")):
        if path.name in SKIP_FILES:
            continue
        m = ADR_FILE_RE.match(path.name)
        if not m:
            err(path, "ADR file name must be NNNN-kebab-title.md")
            continue
        num = m.group(1)
        if num in seen:
            err(path, f"duplicate ADR number {num}")
        seen[num] = path
        text = path.read_text(encoding="utf-8")
        if not re.search(rf"^# ADR-{num}:", text, re.MULTILINE):
            err(path, f"first heading must be '# ADR-{num}: <title>'")
        if not re.search(r"^- \*\*Status:\*\*", text, re.MULTILINE):
            err(path, "missing '- **Status:** ...' line")
        if path.name not in index_text:
            err(ADRS / "README.md", f"ADR {path.name} is not linked from the index")


def check_skills() -> None:
    if not SKILLS.exists():
        return
    for skill_dir in sorted(p for p in SKILLS.iterdir() if p.is_dir()):
        skill = skill_dir / "SKILL.md"
        if not skill.exists():
            err(skill_dir, "missing SKILL.md")
            continue
        fm = parse_frontmatter(skill.read_text(encoding="utf-8"))
        if not fm:
            err(skill, "missing frontmatter")
            continue
        if fm.get("name") != skill_dir.name:
            err(skill, f"frontmatter name '{fm.get('name')}' must equal directory name '{skill_dir.name}'")
        if not fm.get("description"):
            err(skill, "frontmatter 'description' is required")


def markdown_files() -> list[Path]:
    files = list(DOCS.rglob("*.md")) if DOCS.exists() else []
    for extra in ("AGENTS.md", "server/AGENTS.md", ".github/pull_request_template.md"):
        if (ROOT / extra).exists():
            files.append(ROOT / extra)
    if SKILLS.exists():
        files.extend(SKILLS.rglob("*.md"))
    return sorted(set(files))


def check_links() -> None:
    for path in markdown_files():
        if path.name == "_TEMPLATE.md":
            continue  # links are relative to where copies will live, not the template itself
        text = strip_code_blocks(path.read_text(encoding="utf-8"))
        for target in LINK_RE.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue  # external URL, mailto:, file://, or same-page anchor
            clean = target.split("#", 1)[0]
            if not clean:
                continue
            resolved = (path.parent / clean).resolve()
            if not resolved.exists():
                err(path, f"broken link -> {target}")


def main() -> int:
    specs = check_specs()
    check_spec_index(specs)
    check_bug_refs(specs)
    check_adrs()
    check_skills()
    check_links()

    by_status: dict[str, int] = {}
    for info in specs.values():
        by_status[info["status"]] = by_status.get(info["status"], 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())) or "none"

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"\nspecs: {len(specs)} ({summary}) | errors: {len(errors)} | warnings: {len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
