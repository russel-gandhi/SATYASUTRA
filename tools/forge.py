"""Ticket CLI for FORGE. Tickets are markdown files in tickets/ with a YAML header.

    python tools/forge.py board
    python tools/forge.py list [--phase N] [--status todo|doing|done]
    python tools/forge.py show F0-01
    python tools/forge.py start F0-01
    python tools/forge.py done F0-01 [--evidence "command that passed"]
    python tools/forge.py new --phase 1 --title "..." [--depends F0-01 F0-02]
    python tools/forge.py check            # validates ids, statuses, dependency graph

Rules the tool enforces: a ticket can start only when its dependencies are done; a ticket can be
closed only if it was started; closing records the date, the evidence and the current commit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "tickets"
STATUSES = ("todo", "doing", "done")
HEADER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
ID = re.compile(r"\AF(\d+)-(\d+)\Z")

TEMPLATE = """---
id: {id}
phase: {phase}
title: {title}
status: todo
depends: {depends}
plan: {plan}
---
## Goal

## Done when

## Notes
"""


class Ticket:
    def __init__(self, path: Path):
        self.path = path
        match = HEADER.match(path.read_text(encoding="utf-8"))
        if not match:
            raise ValueError(f"{path.name}: missing YAML header")
        self.meta = yaml.safe_load(match.group(1)) or {}
        self.body = match.group(2)

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def status(self) -> str:
        return self.meta["status"]

    def save(self) -> None:
        header = yaml.safe_dump(self.meta, sort_keys=False, allow_unicode=True).rstrip()
        self.path.write_text(f"---\n{header}\n---\n{self.body}", encoding="utf-8")


def load_all() -> dict[str, Ticket]:
    tickets: dict[str, Ticket] = {}
    for path in sorted(TICKETS.glob("F*.md")):
        ticket = Ticket(path)
        if ticket.id in tickets:
            raise ValueError(f"duplicate ticket id {ticket.id}")
        tickets[ticket.id] = ticket
    return tickets


def current_commit() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    )
    return out.stdout.strip() or "uncommitted"


def validate(tickets: dict[str, Ticket]) -> list[str]:
    problems = []
    for t in tickets.values():
        if not ID.match(t.id):
            problems.append(f"{t.id}: id must look like F<phase>-<nn>")
        if t.status not in STATUSES:
            problems.append(f"{t.id}: bad status {t.status!r}")
        if t.path.stem != t.id:
            problems.append(f"{t.id}: file name must be {t.id}.md")
        for dep in t.meta.get("depends") or []:
            if dep not in tickets:
                problems.append(f"{t.id}: unknown dependency {dep}")
        if t.status == "done" and "## Closed" not in t.body:
            problems.append(f"{t.id}: done but has no Closed section")
    return problems


def cmd_board(tickets):
    phases = sorted({int(t.meta["phase"]) for t in tickets.values()})
    for phase in phases:
        group = [t for t in tickets.values() if int(t.meta["phase"]) == phase]
        counts = {s: sum(1 for t in group if t.status == s) for s in STATUSES}
        print(f"Phase {phase}: {counts['done']}/{len(group)} done, {counts['doing']} doing, {counts['todo']} todo")
        for t in group:
            mark = {"todo": "[ ]", "doing": "[~]", "done": "[x]"}[t.status]
            print(f"  {mark} {t.id}  {t.meta['title']}")


def cmd_list(tickets, args):
    for t in tickets.values():
        if args.phase is not None and int(t.meta["phase"]) != args.phase:
            continue
        if args.status and t.status != args.status:
            continue
        print(f"{t.id}\t{t.status}\t{t.meta['title']}")


def cmd_show(tickets, args):
    print(tickets[args.id].path.read_text(encoding="utf-8"))


def cmd_start(tickets, args):
    t = tickets[args.id]
    if t.status != "todo":
        sys.exit(f"{t.id} is {t.status}, not todo")
    blocked = [d for d in t.meta.get("depends") or [] if tickets[d].status != "done"]
    if blocked:
        sys.exit(f"{t.id} is blocked by: {', '.join(blocked)}")
    t.meta["status"] = "doing"
    t.save()
    print(f"started {t.id}")


def cmd_done(tickets, args):
    t = tickets[args.id]
    if t.status != "doing":
        sys.exit(f"{t.id} is {t.status}; start it first")
    t.meta["status"] = "done"
    evidence = args.evidence or "see commit"
    t.body = t.body.rstrip() + (
        f"\n\n## Closed\n\n- Date: {dt.date.today().isoformat()}\n"
        f"- Evidence: {evidence}\n- Based on commit: {current_commit()}\n"
    )
    t.save()
    print(f"closed {t.id}")


def cmd_new(tickets, args):
    nums = [int(ID.match(i).group(2)) for i in tickets if ID.match(i) and int(ID.match(i).group(1)) == args.phase]
    new_id = f"F{args.phase}-{(max(nums) + 1 if nums else 1):02d}"
    path = TICKETS / f"{new_id}.md"
    path.write_text(
        TEMPLATE.format(
            id=new_id, phase=args.phase, title=args.title,
            depends=yaml.safe_dump(args.depends or [], default_flow_style=True).strip(),
            plan=args.plan or "",
        ),
        encoding="utf-8",
    )
    print(f"created tickets/{path.name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("board")
    sub.add_parser("check")
    p = sub.add_parser("list"); p.add_argument("--phase", type=int); p.add_argument("--status", choices=STATUSES)
    for name in ("show", "start", "done"):
        p = sub.add_parser(name); p.add_argument("id")
        if name == "done":
            p.add_argument("--evidence")
    p = sub.add_parser("new")
    p.add_argument("--phase", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--depends", nargs="*")
    p.add_argument("--plan")
    args = parser.parse_args(argv)

    tickets = load_all()
    problems = validate(tickets)
    if args.cmd == "check":
        for line in problems:
            print("PROBLEM", line)
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if problems and args.cmd != "show":
        for line in problems:
            print("PROBLEM", line, file=sys.stderr)
        return 1
    if getattr(args, "id", None) and args.id not in tickets:
        sys.exit(f"unknown ticket {args.id}")
    {"board": lambda: cmd_board(tickets), "list": lambda: cmd_list(tickets, args),
     "show": lambda: cmd_show(tickets, args), "start": lambda: cmd_start(tickets, args),
     "done": lambda: cmd_done(tickets, args), "new": lambda: cmd_new(tickets, args)}[args.cmd]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
