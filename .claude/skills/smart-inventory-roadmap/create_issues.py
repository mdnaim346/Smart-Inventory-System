#!/usr/bin/env python3
"""Create the roadmap epics/features from features.json on GitHub.

- Issues and native sub-issue links use the REST API (saves GraphQL quota).
- Board fields come from .github/project-config.json (Status, Priority, Size, Estimate).
- Idempotent: an existing issue whose title starts with the same serial (E1.2 ...) is reused.
- Writes created.json next to this script.

Usage (from repo root): python3 .claude/skills/smart-inventory-roadmap/create_issues.py [--dry-run]
"""
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPEC = json.loads((HERE / "features.json").read_text())
CFG = json.loads((ROOT / ".github" / "project-config.json").read_text())
REPO = SPEC["repo"]
DRY = "--dry-run" in sys.argv
POINTS = {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8}
PRIO_LABEL = {"P0": "priority:p0-critical", "P1": "priority:p1-high", "P2": "priority:p2-medium", "P3": "priority:p3-low"}

TECH_NOTES = f"""- Odoo 19 addon `{CFG['conventions']['moduleName']}` (Python models, XML views, OWL dashboard in `static/src`).
- Storable products: use `is_storable` (Odoo 19), not `type == 'product'` / `detailed_type`.
- New models need rows in `security/ir.model.access.csv` and an entry in `__manifest__.py` `data`.
- Tests: `{CFG['conventions']['testCommand']}`"""


def gh(*args, data=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=data)
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()}")
    return r.stdout


def rest(method, path, payload=None):
    if DRY and method != "GET":
        print(f"  DRY {method} {path}")
        return {"number": 0, "id": 0, "html_url": ""}
    args = ["api", "-X", method, path]
    if payload is not None:
        args += ["--input", "-"]
    out = gh(*args, data=json.dumps(payload) if payload is not None else None)
    return json.loads(out) if out.strip() else {}


def existing():
    out = gh("api", "--paginate", f"repos/{REPO}/issues?state=all&per_page=100", "--jq", ".[]|{number,id,title,html_url}")
    return [json.loads(row) for row in out.splitlines() if row.strip()]


def find(issues, serial, kind):
    prefix = f"[{kind}] {serial} "
    return next((i for i in issues if i["title"].startswith(prefix)), None)


def bullets(items, empty="None"):
    return "\n".join(f"- {x}" for x in items) if items else empty


def checks(items, empty):
    return "\n".join(f"- [ ] {x}" for x in items) if items else empty


def files_table(paths):
    rows = "\n".join(f"| `{p.split(' ')[0]}` | {'Create' if '(new)' in p else 'Modify'} |" for p in paths)
    return "| Path | Action |\n| --- | --- |\n" + rows


def feature_body(epic, epic_no, serial, f):
    return f"""> **One-line summary:** {f['summary']}

**Parent:** #{epic_no} — {epic['key']} {epic['title']}
**Serial:** `{serial}`

## Scope

### In Scope
{bullets(f['scope'])}

### Out of Scope
- See parent epic

## Acceptance Criteria

{checks(f['ac'], '')}
- [ ] Tests added and passing
- [ ] Module upgrades cleanly (`-u smart_inventory`)

## Files to Modify/Create

{files_table(f['files'])}

## Technical Notes

{TECH_NOTES}

## Estimate

Size **{f['size']}** — {POINTS[f['size']]} story points

## Needs Clarity

{checks(f['clarity'], 'None — requirements are clear.')}
"""


def epic_body(epic, children):
    subs = "\n".join(f"- [ ] #{n} — {t}" for n, t, _ in children) or "_Filled in after sub-issues are created._"
    pts = sum(p for *_, p in children) or sum(POINTS[f["size"]] for f in epic["features"])
    return f"""> **One-line summary:** {epic['summary']}

## Overview

{epic['overview']}

## Scope

### In Scope
{bullets(f"{epic['key']}.{i} {f['title']}" for i, f in enumerate(epic['features'], 1))}

### Out of Scope
{bullets(epic['out_of_scope'])}

## Acceptance Criteria

- [ ] All sub-issues closed
- [ ] All tests pass on CI
- [ ] README updated for the new behaviour

## Technical Notes

{TECH_NOTES}

## Sub-Issues

{subs}

## Estimate

{len(epic['features'])} items, **{pts} story points**.

## Needs Clarity

None — see each sub-issue.
"""


class Board:
    def __init__(self):
        self.p = CFG["project"]
        self.f = CFG["fields"]

    def on_board(self):
        out = gh("project", "item-list", str(self.p["number"]), "--owner", CFG["owner"], "--format", "json", "--limit", "500")
        return {i["content"].get("number") for i in json.loads(out)["items"]}

    def add(self, url, status, prio, size, pts):
        if DRY:
            return
        item = json.loads(gh("project", "item-add", str(self.p["number"]), "--owner", CFG["owner"], "--url", url, "--format", "json"))["id"]
        status_opt = self.f["Status"]["options"][self.f["Status"]["workflow"][status]]
        for fid, opt in ((self.f["Status"]["id"], status_opt),
                         (self.f["Priority"]["id"], self.f["Priority"]["options"][prio]),
                         (self.f["Size"]["id"], self.f["Size"]["options"][size])):
            gh("project", "item-edit", "--project-id", self.p["id"], "--id", item, "--field-id", fid, "--single-select-option-id", opt)
        gh("project", "item-edit", "--project-id", self.p["id"], "--id", item, "--field-id", self.f["Estimate"]["id"], "--number", str(pts))


def upsert(issues, board, serial, kind, title, body, labels, prio, size, pts):
    hit = find(issues, serial, kind)
    if hit:
        print(f"= reuse #{hit['number']} {hit['title']}")
        return hit
    full = f"[{kind}] {serial} {title}"
    res = rest("POST", f"repos/{REPO}/issues", {"title": full, "body": body, "labels": labels})
    hit = {"number": res["number"], "id": res["id"], "title": full, "html_url": res["html_url"]}
    print(f"+ #{hit['number']} {full}")
    issues.append(hit)
    return hit


def link(parent, child):
    try:
        rest("POST", f"repos/{REPO}/issues/{parent['number']}/sub_issues", {"sub_issue_id": child["id"]})
    except RuntimeError as e:
        if "already" not in str(e).lower() and "duplicate" not in str(e).lower():
            print(f"  ! link #{parent['number']} <- #{child['number']}: {e}")


def main():
    issues = existing()
    board = Board()
    created = []
    board_todo = []
    base = ["repo:smart-inventory", "domain:inventory"]
    for epic in SPEC["epics"]:
        pts = sum(POINTS[f["size"]] for f in epic["features"])
        e = upsert(issues, board, epic["key"], "Epic", epic["title"], epic_body(epic, []),
                   base + ["type:epic", PRIO_LABEL[epic["priority"]]], epic["priority"], "XL", pts)
        children = []
        for i, f in enumerate(epic["features"], 1):
            serial = f"{epic['key']}.{i}"
            labels = sorted(set(base + f["labels"] + [f"type:{f['type'].lower()}", PRIO_LABEL[f["priority"]], f"size:{f['size'].lower()}"]))
            c = upsert(issues, board, serial, f["type"], f["title"], feature_body(epic, e["number"], serial, f),
                       labels, f["priority"], f["size"], POINTS[f["size"]])
            link(e, c)
            children.append((c["number"], f"{serial} {f['title']}", POINTS[f["size"]]))
            created.append({"serial": serial, "number": c["number"], "url": c["html_url"], "epic": e["number"]})
        rest("PATCH", f"repos/{REPO}/issues/{e['number']}", {"body": epic_body(epic, children)})
        created.append({"serial": epic["key"], "number": e["number"], "url": e["html_url"], "points": pts})
        board_todo.append((e, epic["priority"], "XL", pts))
        board_todo.extend((c, f["priority"], f["size"], POINTS[f["size"]]) for c, f in zip(
            [find(issues, f"{epic['key']}.{i}", f["type"]) for i, f in enumerate(epic["features"], 1)], epic["features"]))
    # Board sync last: GraphQL can be flaky/rate-limited; rerun the script to retry.
    if not DRY:
        try:
            present = board.on_board()
            for issue, prio, size, pts in board_todo:
                if issue["number"] not in present:
                    board.add(issue["html_url"], "Backlog", prio, size, pts)
                    print(f"  board: #{issue['number']}")
        except RuntimeError as err:
            print(f"! board sync stopped ({err}); rerun the script later to finish")
    if not DRY:
        (HERE / "created.json").write_text(json.dumps(created, indent=2) + "\n")
    n = sum(len(x["features"]) for x in SPEC["epics"])
    print(f"\nDone: {len(SPEC['epics'])} epics, {n} sub-issues.")


if __name__ == "__main__":
    main()
