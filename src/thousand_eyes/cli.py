"""thousand-eyes - wildfire self-audit CLI (v0.5).

Impact-ranked, evidence-only lead-driven pivot graph. Default mode is LLM-free
and keyless; `--agent` adds an LLM-planned pipeline (Gemini flash-lite, free
tier) on top of the same fenced tools. No breach dumps, no credential probes,
no password-reset flows. Uses only keyless, read-only, first-party public
endpoints by default.

Usage:
    thousand-eyes                               # purple chat TUI (interactive agent)
    thousand-eyes you@example.com                 # polite sweep
    thousand-eyes you@example.com --full          # wildfire: caps + full WMN sweep + derived handles
    thousand-eyes --agent you@example.com         # agentic: LLM-planned batches + markdown dossier
    thousand-eyes --demo you@example.com          # offline synthetic replay (venue-safe)
    thousand-eyes you@example.com --json r.json --html r.html
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from thousand_eyes import __version__
from thousand_eyes.normalise import InvalidSeed, normalise_seed

DISCLAIMER = """\
thousand-eyes v{v} - public-data self-audit (wildfire edition)
  * reads only PUBLIC, keyless, first-party endpoints (gravatar, github,
    twitter availability, microsoft userrealm, xposedornot, keys.openpgp,
    web.archive, python, reddit .json, crt.sh/DNS, bing html, ahmia index,
    WhatsMyName dataset)
  * built for identifiers you own or have the owner's consent to scan -
    scanning third parties stays your responsibility
  * no password-reset probing, no credential checks, no breach dumps,
    no broker APIs, no following .onion links - enforced in code
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thousand-eyes",
        description="Wildfire self-audit CLI - one lead in, an evidence-ranked web of public proof out.",
        epilog="Self-audit: scan identifiers you own or are authorized to review.",
    )
    parser.add_argument("--version", action="version", version=f"thousand-eyes {__version__}",
                        help="print the version stamp and exit")
    parser.add_argument("targets", nargs="*", help="one or more leads: email, E.164 phone, https URL, or username")
    parser.add_argument("--full", action="store_true",
                        help="wildfire mode: wider spawn caps + full 692-site WMN sweep + derived local-part handles")
    parser.add_argument("--json", metavar="PATH", help="write the full report as JSON")
    parser.add_argument("--html", metavar="PATH", help="write a standalone one-file HTML dossier (renders the graph)")
    parser.add_argument("--dot", metavar="PATH", help="write a Graphviz .dot file")
    parser.add_argument("--fold-plus", action="store_true", help="strip +tag from email local-parts before scanning")
    parser.add_argument("--depth", type=int, default=3, help="how deep spawned leads are chased (default 3)")
    parser.add_argument("--no-banner", action="store_true", help="skip the animated startup banner + live cockpit")
    parser.add_argument("--verbose", "-v", action="store_true", help="print the full evidence dump after the ranking")
    parser.add_argument("--demo", action="store_true", help="offline synthetic replay (venue-safe; fictional subject + your seeds)")
    parser.add_argument("--url-log", metavar="PATH", help="append every outbound request URL to this file (transparency check)")
    parser.add_argument("--agent", action="store_true",
                        help="agentic mode: LLM-planned tool batches + evidence ledger + markdown dossier "
                             "(Gemini flash-lite via GEMINI_API_KEY, free tier)")
    parser.add_argument("--model", default=None,
                        help="agent mode: model id (default gemini-3.5-flash-lite; flash-lite = free tier)")
    parser.add_argument("--agent-budget", type=int, default=120,
                        help="agent mode: max LLM calls (default 120)")
    parser.add_argument("--budget-seconds", type=int, default=900,
                        help="wall-clock budget for the cascade (default 900s = 15 min; "
                             "quality first - raise it for a deeper sweep)")
    parser.add_argument("--max-leads", type=int, default=0,
                        help="hard cap on total leads processed (0 = unlimited, "
                             "the time budget is the real governor)")
    parser.add_argument("--setup", action="store_true",
                        help="guided onboarding: pick a provider and store "
                             "your LLM key (gemini free tier or anthropic)")
    parser.add_argument("--skill", metavar="ACTION", choices=("install", "uninstall", "status"),
                        help="install/uninstall this CLI as an IDE-agent skill "
                             "(writes ~/.claude/skills/thousand-eyes)")
    parser.add_argument("--doctor", action="store_true",
                        help="check this machine: runtime, deps, LLM key, IDE skill "
                             "and endpoint reachability")
    return parser


def _run_agent(args: argparse.Namespace, transport=None) -> int:
    """Agentic self-audit: LLM-planned batches, evidence ledger, markdown dossier."""
    import time

    from thousand_eyes.agent import Planner, client_for

    try:
        llm = client_for(args.model)
    except RuntimeError as exc:
        print(f"thousand-eyes agent: {exc}", file=sys.stderr)
        return 2

    leads = []
    for raw in args.targets:
        try:
            seed = normalise_seed(raw)
        except InvalidSeed as exc:
            print(f"thousand-eyes agent: {exc}", file=sys.stderr)
            return 2
        leads.append(seed.value)

    async def show(ev):  # mirror planner events onto stderr / live cockpit
        kind, data = ev
        if kind == "round":
            print(f"agent: depth {data.get('depth')} - {data.get('tools')} tools, "
                  f"ledger {data.get('ledger')} rows", file=sys.stderr)
        elif kind == "tool":
            print(f"tool: {data.get('name')} ok={data.get('ok')}", file=sys.stderr)
        elif kind == "audit":
            print(f"audit: next families {data.get('picked')}", file=sys.stderr)
        elif kind in ("start", "ready"):
            print(f"agent: {kind} {data}", file=sys.stderr)
        elif kind in ("llm", "report", "done", "budget"):
            pass  # streaming text + final report are printed at the end
        else:
            print(f"agent: {kind} {data}", file=sys.stderr)

    planner = Planner(llm, handlers=[show], goal="", budget_calls=args.agent_budget,
                      transport=transport)

    sink = None
    if not args.no_banner:
        try:
            from thousand_eyes.live import RichSink
            sink = RichSink()
        except Exception:
            sink = None

    start = time.monotonic()
    try:
        if sink is not None:
            with sink:
                report = asyncio.run(planner.run(leads))
        else:
            report = asyncio.run(planner.run(leads))
    except RuntimeError as exc:  # rate limit, key rejected, network
        print(f"thousand-eyes agent: LLM error: {exc}", file=sys.stderr)
        return 3
    finally:
        asyncio.run(planner.close())
    wall = time.monotonic() - start

    if args.demo:
        report = "*** DEMO MODE - SYNTHETIC fixture replay, zero network calls ***\n\n" + report
    print(report)
    if args.json:
        import json as _json
        with open(args.json, "w", encoding="utf-8") as fh:
            _json.dump({"version": __version__, "mode": "agent", "wall_s": round(wall, 2),
                        "analytics": planner.analytics.summary(),
                        "report_md": report, "evidence": planner.ledger},
                       fh, indent=1)
        print(f"\nfull agent report written to {args.json}", file=sys.stderr)
    if args.html:
        from thousand_eyes.html_report import render_html
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html({"report_text": report}, version=__version__))
        print(f"dossier written to {args.html}", file=sys.stderr)
    return 0


def print_banner() -> None:
    """Play the intro once and stamp the version line - tty only. In
    non-tty runs (pipes/CI) print_banner prints NOTHING; the ranked report
    itself carries 'THOUSAND EYES vX' in its header anyway."""
    if not sys.stdout.isatty():
        return
    try:
        from thousand_eyes.banner import show_banner
        show_banner(loops=1)
    except Exception:
        print("\x1b[1;38;2;200;64;255mTHOUSAND EYES\x1b[0m")
    purple = "\x1b[1;38;2;204;66;255m"
    ghost = "\x1b[38;2;140;92;252m"
    reset = "\x1b[0m"
    print(f"{purple}✦ thousand-eyes\x1b[0m {ghost}v{__version__} · evidence-only wildfire · no LLMs · no credential probes{reset}")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252 and choke on the ranked-report
    # glyphs - force utf-8 with a lenient error handler so output never dies.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = build_parser()
    args_list = sys.argv[1:] if argv is None else list(argv)
    if any(a in ("-h", "--help") for a in args_list):
        print(DISCLAIMER.format(v=__version__))
    args = parser.parse_args(args_list)

    # onboarding first - no targets, no scan, just the guided flow
    if args.setup:
        from thousand_eyes.setup import prompt_key
        return prompt_key()
    if args.doctor:
        from thousand_eyes.doctor import run_doctor
        return run_doctor()
    if args.skill:
        from thousand_eyes.skill import install_skill, skill_status, uninstall_skill
        if args.skill == "install":
            for p in install_skill():
                print(f"skill written: {p}")
            print("restart your IDE session to pick it up.")
            return 0
        if args.skill == "uninstall":
            removed = uninstall_skill()
            print("\n".join(f"skill removed: {p}" for p in removed) or
                  "skill was not installed")
            return 0
        print(f"thousand-eyes {__version__} - {skill_status()}")
        return 0

    # bare `thousand-eyes` - the purple chat TUI
    if not args.targets and not any(
            a in args_list for a in ("--agent", "--demo", "--json", "--html", "--dot",
                                     "--url-log", "--help", "-h")):
        from thousand_eyes.tui import ThousandEyesApp
        return ThousandEyesApp(model=args.model, budget=args.agent_budget).run()

    if not args.targets:  # flags without leads can't run
        print("thousand-eyes: error: the following arguments are required: targets",
              file=sys.stderr)
        return 2

    errors: list[str] = []
    for target in args.targets:
        try:
            normalise_seed(target, fold_plus=args.fold_plus)
        except InvalidSeed as exc:
            errors.append(str(exc))
    if errors:
        for e in errors:
            print(f"thousand-eyes: {e}", file=sys.stderr)
        return 2

    is_tty = sys.stdout.isatty() and not args.no_banner

    if is_tty:
        print_banner()

    from thousand_eyes.demo import demo_transport
    transport = demo_transport() if args.demo else None

    if args.agent:
        return _run_agent(args, transport)

    # lazy: the scan stack (httpx et al) is heavy - import it only when a
    # scan actually runs, so --help/--setup/--skill/--version boot instantly
    from thousand_eyes.output import render_dot, render_json, render_text
    from thousand_eyes.scan import run_scan

    wild = args.full
    sink = None
    try:
        if is_tty:
            from thousand_eyes.live import RichSink
            try:
                sink = RichSink()
            except Exception:
                sink = None
        text = None
        if sink is not None:
            with sink:
                result_obj = asyncio.run(run_scan(
                    raw_seeds=list(args.targets),
                    fold_plus=args.fold_plus,
                    max_depth=max(0, args.depth),
                    wild=wild,
                    all_sites=wild,
                    transport=transport,
                    url_log=args.url_log,
                    sink=sink,
                    time_budget=float(args.budget_seconds),
                    max_leads=args.max_leads or None,
                ))
        else:
            result_obj = asyncio.run(run_scan(
                raw_seeds=list(args.targets),
                fold_plus=args.fold_plus,
                max_depth=max(0, args.depth),
                wild=wild,
                all_sites=wild,
                transport=transport,
                url_log=args.url_log,
                time_budget=float(args.budget_seconds),
                max_leads=args.max_leads or None,
            ))
    except Exception as exc:  # never show a bare traceback to a user mid-demo
        print(f"scan failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    text = render_text(result_obj, verbose=args.verbose, version=__version__)
    if args.demo:
        text = "*** DEMO MODE - SYNTHETIC fixture replay, zero network calls ***\n\n" + text
    print(text)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(render_json(result_obj))
        print(f"\nfull report written to {args.json}")

    if args.html:
        from thousand_eyes.html_report import render_html
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html(result_obj, version=__version__))
        print(f"dossier written to {args.html}")

    if args.dot:
        with open(args.dot, "w", encoding="utf-8") as fh:
            fh.write(render_dot(result_obj))
        print(f"graph written to {args.dot}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
