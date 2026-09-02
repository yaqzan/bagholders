"""queue_manager — `trader queue ...` CLI for the task_queue subsystem.

Delegated to from trader.py exactly like algorithm_versions/manager.py. Built on
argparse subparsers. The one wrinkle vs. the other managers: `queue submit`
captures an arbitrary command after `--`, and trader.py's join-then-resplit of
argv would lose quoting — so we recover the verbatim command tail straight from
sys.argv (the process argv is intact; only trader.py's reconstructed string is
lossy).

Subcommands:
    submit   queue a command           list     show tasks
    status   daemon + budget snapshot  show     full detail + log tail
    logs     tail a task's output      wait     block til terminal (bg-alert bridge)
    kill     stop a running task       cancel   cancel/stop a task
    priority re-prioritize a task
    hold     pause (don't admit)       release  un-hold
    clear    bulk-cancel queued        daemon   run the persistent scheduler
    tick     one reconcile pass (cron-mode alternative to daemon)
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from datetime import datetime

from task_queue import api as queue_lib
from task_queue.model import State, priority_name


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #
def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600:02d}h"


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dur(s) -> float | None:
    """'30s' / '5m' / '2h' / '1d' / bare-seconds -> seconds; None/'' -> None."""
    if s is None:
        return None
    s = str(s).strip().lower()
    if not s:
        return None
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        return float(s[:-1]) * mult[s[-1]] if s[-1] in mult else float(s)
    except (ValueError, KeyError):
        return None


def _runtime(t) -> str:
    if t.state == State.RUNNING and t.started_at:
        return _fmt_age(time.time() - t.started_at)
    if t.started_at and t.finished_at:
        return _fmt_age(t.finished_at - t.started_at)
    if t.state == "queued":
        return "waiting " + _fmt_age(time.time() - t.created_at)
    return "-"


def _cmd_str(t, width: int = 48) -> str:
    s = " ".join(t.argv)
    return s if len(s) <= width else s[: width - 1] + "…"


def _pending_intents(t) -> str:
    bits = []
    if t.cancel_requested or t.kill_requested:
        bits.append("cancel?")
    if t.target_priority is not None:
        bits.append(f"->{priority_name(t.target_priority)}")
    if t.hold_requested:
        bits.append("hold?")
    if t.release_requested:
        bits.append("release?")
    if t.held:
        bits.append("HELD")
    return ",".join(bits)


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #
def cmd_submit(args: argparse.Namespace) -> None:
    command = list(getattr(args, "_command_argv", []) or [])
    if not command:
        print("error: provide the command after `--`, e.g.")
        print('  trader queue submit --priority low -- python tools/build_research_pack.py --version v46')
        raise SystemExit(2)
    env = {}
    for kv in args.env or []:
        if "=" not in kv:
            print(f"error: --env expects KEY=VALUE, got {kv!r}")
            raise SystemExit(2)
        k, v = kv.split("=", 1)
        env[k] = v
    res = queue_lib.submit(
        command,
        priority=args.priority,
        cpu=args.cpu,
        db=args.db,
        io=args.io,
        window=args.window,
        restartable=args.restartable,
        exclusive=args.exclusive,
        dedup=args.dedup,
        ttl=args.ttl,
        timeout=args.timeout,
        not_before=args.not_before,
        max_attempts=args.max_attempts,
        reason=args.reason,
        created_by=args.by,
        env=env or None,
        cwd=args.cwd,
        staleness_check=shlex.split(args.staleness_check) if args.staleness_check else None,
    )
    t = res.task
    if res.created:
        print(f"queued task #{t.id}  priority={priority_name(t.priority)}  cpu={t.cpu_request}  "
              f"db={t.db_class}  cmd: {_cmd_str(t, 80)}")
    else:
        print(f"deduped: active task #{t.id} already exists for dedup_key={t.dedup_key!r} "
              f"(state={t.state}). Nothing queued.")
    h = queue_lib.daemon_health()
    if not h["alive"]:
        print(f"note: queue daemon is not running ({h['reason']}). "
              f"Start it with `trader queue daemon` (or run `trader queue tick`).")


def cmd_list(args: argparse.Namespace) -> None:
    states = None
    if args.state:
        states = [args.state]
    elif not args.all:
        states = [State.QUEUED, State.LAUNCHING, State.RUNNING, State.SUSPENDED]
    tasks = queue_lib.list_tasks(states=states, limit=args.limit)
    if not tasks:
        print("no tasks." if args.all else "no active tasks (use --all to see finished).")
        return
    print(f"{'ID':>4}  {'STATE':<9} {'PRIO':<9} {'CPU':<3} {'DB':<5} {'RUNTIME':<14} {'CMD':<48} INTENTS")
    print("-" * 110)
    for t in tasks:
        print(f"{t.id:>4}  {t.state:<9} {priority_name(t.priority):<9} "
              f"{(t.cpu_grant if t.cpu_grant else t.cpu_request)!s:<3} {t.db_class:<5} "
              f"{_runtime(t):<14} {_cmd_str(t):<48} {_pending_intents(t)}")


def cmd_status(args: argparse.Namespace) -> None:
    st = queue_lib.status()
    d = st["daemon"]
    r = st["resources"]
    flag = "OK" if d["alive"] else "DOWN"
    print(f"daemon: [{flag}] {d['reason']}"
          + (f"  pid={d['pid']} heartbeat={_fmt_age(d.get('heartbeat_age_s'))} ago" if d.get("pid") else ""))
    b, u, f = r["budget"], r["used"], r["free"]
    print(f"cores : {u['cores']}/{b['cores']} used ({f['cores']} free)   "
          f"db: {u['db']}/{b['db']}   io: {u['io']}/{b['io']}   "
          f"(low-pri core cap {r['low_priority_core_cap']}, cpu_count {r['cpu_count']})")
    counts = st["counts"]
    if counts:
        order = [State.RUNNING, State.SUSPENDED, State.LAUNCHING, State.QUEUED,
                 State.DONE, State.FAILED, State.CANCELLED]
        parts = [f"{s}={counts[s]}" for s in order if s in counts]
        parts += [f"{s}={c}" for s, c in counts.items() if s not in order]
        print("tasks : " + "  ".join(parts))
    else:
        print("tasks : (none)")


def cmd_show(args: argparse.Namespace) -> None:
    from task_queue.launcher import tail_log
    from task_queue.store import QueueStore

    with QueueStore() as st:
        t = st.get_task(args.id)
        if not t:
            print(f"no task #{args.id}")
            raise SystemExit(1)
        print(f"task #{t.id}  [{t.state}]  priority={priority_name(t.priority)}")
        print(f"  command : {' '.join(t.argv)}")
        print(f"  cwd     : {t.cwd or '(project root)'}")
        print(f"  resources: cpu_request={t.cpu_request} cpu_grant={t.cpu_grant} "
              f"db={t.db_class} io={t.io_class} window={t.time_window}")
        print(f"  flags   : restartable={bool(t.restartable)} exclusive={bool(t.exclusive)} "
              f"held={bool(t.held)} dedup={t.dedup_key}")
        _to = f"{t.timeout_s}s" if t.timeout_s else "-"
        _tl = f"{t.ttl_s}s" if t.ttl_s else "-"
        print(f"  attempts: {t.attempts}/{t.max_attempts}  timeout={_to} ttl={_tl}")
        print(f"  pid     : {t.pid}  run_dir={t.run_dir}")
        print(f"  created : {_fmt_ts(t.created_at)} by {t.created_by or '-'}  reason: {t.reason or '-'}")
        print(f"  started : {_fmt_ts(t.started_at)}  finished: {_fmt_ts(t.finished_at)}  "
              f"exit={t.exit_code}")
        if t.error:
            print(f"  error   : {t.error}")
        intents = _pending_intents(t)
        if intents:
            print(f"  pending : {intents}")
        evs = st.recent_events(t.id, limit=12)
        if evs:
            print("  events  :")
            for e in reversed(evs):
                print(f"    {_fmt_ts(e['ts'])}  {e['kind']:<12} {e['detail']}")
        if t.run_dir:
            tail = tail_log(t.run_dir, lines=args.lines)
            if tail:
                print(f"  --- run.log (last {args.lines}) ---")
                for line in tail.splitlines():
                    print("    " + line)


def cmd_logs(args: argparse.Namespace) -> None:
    from task_queue.launcher import F_ERRLOG, F_RUNLOG, tail_log

    t = queue_lib.get(args.id)
    if not t or not t.run_dir:
        print(f"no run directory for task #{args.id}")
        raise SystemExit(1)
    name = F_ERRLOG if args.stderr else F_RUNLOG
    out = tail_log(t.run_dir, name=name, lines=args.lines)
    print(out if out else f"(empty {name})")


def cmd_wait(args: argparse.Namespace) -> None:
    """Block until a task is terminal, then print its result and exit
    (0=done, 1=failed/cancelled, 2=timeout/not-found). The queue is NOT
    harness-notified, so run THIS with the harness background flag (Bash/PowerShell
    `run_in_background`): it polls internally and exits on completion, which
    re-invokes the agent. This is the canonical 'alert me when the task is done'
    bridge — one command instead of a hand-rolled poll loop."""
    from task_queue.launcher import tail_log

    terminal = {State.DONE, State.FAILED, State.CANCELLED}
    interval = _parse_dur(args.interval) or 60.0
    timeout_s = _parse_dur(args.timeout)
    deadline = (time.time() + timeout_s) if timeout_s else None

    t = queue_lib.get(args.id)
    if not t:
        print(f"no task #{args.id}")
        raise SystemExit(2)
    while t.state not in terminal:
        if deadline and time.time() >= deadline:
            print(f"task #{args.id} still [{t.state}] after timeout ({args.timeout}); stopped waiting.")
            raise SystemExit(2)
        time.sleep(interval)
        t = queue_lib.get(args.id) or t

    print(f"task #{t.id} [{t.state}] exit={t.exit_code} runtime={_runtime(t)}  cmd: {_cmd_str(t, 80)}")
    if t.error:
        print(f"  error: {t.error}")
    if t.run_dir:
        tail = tail_log(t.run_dir, lines=args.lines)
        if tail:
            print(f"  --- run.log (last {args.lines}) ---")
            for line in tail.splitlines():
                print("    " + line)
    raise SystemExit(0 if t.state == State.DONE else 1)


def cmd_kill(args: argparse.Namespace) -> None:
    ok = queue_lib.kill(args.id)
    print(f"kill requested for task #{args.id} (daemon will stop it next tick)."
          if ok else f"task #{args.id} not found or already finished.")


def cmd_cancel(args: argparse.Namespace) -> None:
    ok = queue_lib.cancel(args.id)
    print(f"cancel requested for task #{args.id}."
          if ok else f"task #{args.id} not found or already finished.")


def cmd_priority(args: argparse.Namespace) -> None:
    ok = queue_lib.set_priority(args.id, args.priority)
    print(f"task #{args.id} re-prioritize -> {args.priority} (applies next tick)."
          if ok else f"task #{args.id} not found or already finished.")


def cmd_hold(args: argparse.Namespace) -> None:
    ok = queue_lib.hold(args.id)
    print(f"hold requested for task #{args.id}." if ok else f"task #{args.id} not found / finished.")


def cmd_release(args: argparse.Namespace) -> None:
    ok = queue_lib.release(args.id)
    print(f"release requested for task #{args.id}." if ok else f"task #{args.id} not found / finished.")


def cmd_clear(args: argparse.Namespace) -> None:
    n = queue_lib.clear(states=[args.state], priority=args.priority)
    print(f"requested cancel of {n} {args.state} task(s)"
          + (f" at priority {args.priority}." if args.priority else "."))


def cmd_daemon(args: argparse.Namespace) -> None:
    from task_queue import daemon

    raise SystemExit(daemon.run_daemon(interval=args.interval))


def cmd_tick(args: argparse.Namespace) -> None:
    from task_queue import daemon

    raise SystemExit(daemon.run_once())


def cmd_run_update(args: argparse.Namespace) -> None:
    """Model-B routing for the scheduled `trader update`. If the daemon is
    healthy, enqueue update at 'scheduled' priority (it preempts low-priority
    work). If the daemon is down, run update INLINE — exactly today's behavior,
    so the trading-day pipeline never depends on the queue being up."""
    import subprocess

    from task_queue.launcher import ROOT

    update_cmd = "close-update" if args.close else "update"
    base_dedup = os.environ.get("TRADER_QUEUE_UPDATE_DEDUP", "trader-update")
    dedup = base_dedup + ("-close" if args.close else "")

    health = queue_lib.daemon_health()
    if health["alive"]:
        res = queue_lib.submit(
            ["python", "trader.py", update_cmd],
            priority="scheduled", db="heavy", cpu=args.cpu,
            exclusive=True, dedup=dedup, restartable=False,  # never auto-requeue (sends alerts)
            timeout=args.timeout, window="any",
            reason=f"scheduled {update_cmd} routed through the queue",
            created_by="task-scheduler",
        )
        if res.created:
            print(f"enqueued {update_cmd} as task #{res.task.id} (priority=scheduled, daemon will run it).")
        else:
            print(f"{update_cmd} already active as task #{res.task.id} (state={res.task.state}); "
                  "exclusive -> not re-queued.")
        return
    # Daemon down -> inline fallback (zero regression vs today).
    print(f"queue daemon down ({health['reason']}); running {update_cmd} inline.")
    rc = subprocess.run([sys.executable, "trader.py", update_cmd], cwd=str(ROOT)).returncode
    raise SystemExit(rc)


def cmd_audit(args: argparse.Namespace) -> None:
    """On-demand deterministic audit (no LLM, no per-tick cost): surfaces
    long-idle, now-stale, duplicate, and failed tasks for review."""
    from task_queue import reaper
    from task_queue.store import QueueStore

    with QueueStore() as st:
        rep = reaper.audit(st, idle_hours=args.idle_hours)
        idle, stale, failed, dupes = (
            rep["long_idle"], rep["stale"], rep["failed"], rep["duplicate_commands"],
        )
        print(f"queue audit (idle threshold {args.idle_hours}h):")
        if idle:
            print(f"  long-idle queued ({len(idle)}):")
            for t in idle:
                print(f"    #{t.id} {priority_name(t.priority):<8} waiting "
                      f"{_fmt_age(time.time() - t.created_at)}  {_cmd_str(t)}  reason: {t.reason or '-'}")
        if stale:
            print(f"  staleness_check says NO LONGER NEEDED ({len(stale)}):")
            for t in stale:
                print(f"    #{t.id} {_cmd_str(t)}")
        if dupes:
            print(f"  duplicate commands ({len(dupes)} group(s)):")
            for cmd, ids in dupes.items():
                print(f"    ids {ids}: {cmd[:70]}")
        if args.show_failed and failed:
            print(f"  recent failures ({len(failed)}):")
            for t in failed:
                print(f"    #{t.id} {_cmd_str(t)}  error: {t.error}")
        if not (idle or stale or dupes):
            print("  nothing suspicious.")
        if args.fix and (idle or stale):
            n = 0
            for t in idle + stale:
                st.update_fields(t.id, cancel_requested=1)
                n += 1
            print(f"  --fix: requested cancel of {n} idle/stale task(s) (daemon applies next tick).")


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_queue_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trader queue")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("submit", help="queue a command (command goes after `--`)")
    p.add_argument("--priority", "-p", default="normal",
                   help="critical|high|scheduled|normal|low|idle (default normal)")
    p.add_argument("--cpu", type=int, default=1, help="cores requested (default 1)")
    p.add_argument("--db", default="none", choices=("none", "light", "heavy"))
    p.add_argument("--io", default="none", choices=("none", "light", "heavy"))
    p.add_argument("--window", default="any", choices=("any", "off_market"))
    p.add_argument("--restartable", action="store_true",
                   help="idempotent: safe to auto-requeue on infra failure")
    p.add_argument("--exclusive", action="store_true",
                   help="only one active task per dedup key (defaults dedup to the command)")
    p.add_argument("--dedup", help="dedup key; a 2nd active submit is a no-op")
    p.add_argument("--ttl", help="cancel if still queued past this (e.g. 24h)")
    p.add_argument("--timeout", help="wall-clock job ceiling (e.g. 2h)")
    p.add_argument("--not-before", dest="not_before", help="delay start by (e.g. 30m)")
    p.add_argument("--max-attempts", dest="max_attempts", type=int, default=3)
    p.add_argument("--reason", help="why this is queued (shown at a glance)")
    p.add_argument("--by", help="who queued it (agent/session id)")
    p.add_argument("--cwd", help="working directory (default project root)")
    p.add_argument("--env", action="append", metavar="KEY=VAL", help="extra env var (repeatable)")
    p.add_argument("--staleness-check", dest="staleness_check",
                   help="command run while queued; EXIT 0 => no longer needed (auto-cancel), "
                        "non-zero/timeout/error => keep (fail-safe)")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--state", choices=(State.QUEUED, State.LAUNCHING, State.RUNNING,
                                       State.SUSPENDED, State.DONE, State.FAILED, State.CANCELLED))
    p.add_argument("--all", action="store_true", help="include finished tasks")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="daemon health + resource budget snapshot")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show", help="full detail + recent events + run.log tail")
    p.add_argument("id", type=int)
    p.add_argument("--lines", type=int, default=20)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("logs", help="tail a task's output")
    p.add_argument("id", type=int)
    p.add_argument("--lines", type=int, default=40)
    p.add_argument("--stderr", action="store_true")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("wait", help="block until a task is terminal then print its result "
                                    "(run with the harness background flag to be alerted on completion)")
    p.add_argument("id", type=int)
    p.add_argument("--timeout", default=None,
                   help="give up waiting after this (e.g. 3h); default: wait indefinitely")
    p.add_argument("--interval", default="60s", help="poll interval (default 60s)")
    p.add_argument("--lines", type=int, default=30)
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("kill", help="stop a running task")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_kill)

    p = sub.add_parser("cancel", help="cancel a queued task / stop a running one")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("priority", help="re-prioritize a task")
    p.add_argument("id", type=int)
    p.add_argument("priority")
    p.set_defaults(func=cmd_priority)

    p = sub.add_parser("hold", help="pause a task (won't be admitted)")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_hold)

    p = sub.add_parser("release", help="un-hold a task")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("clear", help="bulk-cancel matching tasks")
    p.add_argument("--state", default=State.QUEUED)
    p.add_argument("--priority", default=None)
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("daemon", help="run the persistent scheduler (foreground)")
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("tick", help="run one reconcile pass then exit (cron mode)")
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("run-update",
                       help="Model-B: route `trader update` through the queue, inline if daemon down")
    p.add_argument("--close", action="store_true", help="route close-update instead of update")
    p.add_argument("--cpu", type=int, default=4, help="cores to grant the update (default 4)")
    p.add_argument("--timeout", default="45m", help="wall-clock backstop (default 45m)")
    p.set_defaults(func=cmd_run_update)

    p = sub.add_parser("audit", help="deterministic 'what looks stale?' scan (no LLM, run on demand)")
    p.add_argument("--idle-hours", dest="idle_hours", type=float, default=6.0,
                   help="flag queued tasks waiting longer than this (default 6h)")
    p.add_argument("--show-failed", dest="show_failed", action="store_true")
    p.add_argument("--fix", action="store_true", help="request-cancel idle + staleness-flagged tasks")
    p.set_defaults(func=cmd_audit)

    return parser


def _recover_cli_tokens() -> list[str]:
    """Verbatim tokens after the 'queue'/'queues' command word in sys.argv
    (preserves quoting for `submit -- <command>`). Falls back to sys.argv[1:]
    when invoked directly (no 'queue' word present)."""
    raw = list(sys.argv[1:])
    for i, tok in enumerate(raw):
        if tok in ("queue", "queues"):
            return raw[i + 1:]
    return raw


def main(argv=None) -> None:
    if argv is None:
        tokens = _recover_cli_tokens()
    elif isinstance(argv, str):
        tokens = shlex.split(argv, posix=(os.name != "nt"))
    else:
        tokens = list(argv)

    # Split off the trailing `submit -- <command>` payload verbatim.
    command_argv: list[str] = []
    if "--" in tokens:
        i = tokens.index("--")
        tokens, command_argv = tokens[:i], tokens[i + 1:]

    parser = build_queue_parser()
    if not tokens:
        parser.print_help()
        return
    args = parser.parse_args(tokens)
    args._command_argv = command_argv
    if not hasattr(args, "func"):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
