"""thousand-eyes — wild-fire self-audit CLI (v0.3).

Evidence-only lead-driven pivot graph. No LLM, no breach dumps, no credential
probes. Only scan identifiers you own or are explicitly authorized to review.

Usage:
    thousand-eyes you@example.com
    thousand-eyes you@example.com janedoe_ janedoe   # multiple leads
    thousand-eyes you@example.com --depth 3 --wild         # wildfire spread
    thousand-eyes you@example.com --json report.json --dot graph.dot
    thousand-eyes --demo you@example.com                   # offline synthetic replay
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading

from thousand_eyes.banner import show_banner
from thousand_eyes.normalise import InvalidSeed, normalise_seed
from thousand_eyes.output import render_dot, render_json, render_text
from thousand_eyes.scan import run_scan

DISCLAIMER = """\
thousand-eyes v0.3 — public-data self-audit (wildfire edition)
  * reads only PUBLIC, first-party endpoints (gravatar, github, twitter
    availability, microsoft userrealm, xposedornot, keys.openpgp.org,
    web.archive.org, reddit .json, crt.sh/DNS, bing html, WMN dataset)
  * for identifiers you own or have consent to scan
  * no password-reset probing, no credential checking, no broker APIs,
    no breach dumps — excluded in code, not just in docs
  * leaves: snapshots in time; outbound IP is visible to those platforms
"""

PERMISSION_GATE = """\
=== PERMISSION GATE ================================================
Type CONFIRM to certify ONE of the following, or anything else to cancel:
 1) every identifier above belongs to me; or
 2) I have the owner's documented consent for this scan.
Be honest here: scanning third parties is your responsibility, not ours.
====================================================================
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thousand-eyes",
        description="Wildfire self-audit CLI — feeds one lead into a spider-web of public evidence.",
        epilog="Self-audit only: scan identifiers you own or are authorized to review.",
    )
    parser.add_argument("targets", nargs="+", help="one or more leads: email, E.164 phone, https URL, or username")
    parser.add_argument("--json", metavar="PATH", help="write the full report as JSON")
    parser.add_argument("--dot", metavar="PATH", help="write a Graphviz .dot file")
    parser.add_argument("--fold-plus", action="store_true", help="strip +tag from email local-parts before scanning")
    parser.add_argument("--depth", type=int, default=3, help="how deep spawned leads are chased (default 3)")
    parser.add_argument("--wild", action="store_true", help="uncensored breadth: local-part username permutations + full sweep caps")
    parser.add_argument("--all-sites", action="store_true", help="username sweep runs the whole WMN dataset (692 sites), not the politeness cap")
    parser.add_argument("--url-log", metavar="PATH", help="append every outbound request URL to this file (transparency check)")
    parser.add_argument("--demo", action="store_true", help="offline synthetic replay (venue-safe; uses a fictional subject + your seeds)")
    parser.add_argument("--yes", action="store_true", help="skip the interactive permission gate (scripted runs)")
    parser.add_argument("--no-banner", action="store_true", help="skip the animated startup banner")
    return parser


def print_banner() -> None:
    if sys.stdout.isatty():
        show_banner(loops=1)


def _scan_with_banner(run_kwargs: dict, no_banner: bool):
    """Runs the scan (worker thread + banner like before) and returns the result."""
    if no_banner or not sys.stdout.isatty():
        return asyncio.run(run_scan(**run_kwargs))

    done = threading.Event()
    outcome: dict[str, object] = {}

    def work() -> None:
        try:
            outcome["result"] = asyncio.run(run_scan(**run_kwargs))
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    threading.Thread(target=work, name="thousand-eyes-scan", daemon=True).start()
    show_banner(until=done.is_set)
    done.wait()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def _gate(targets: list[str], demo: bool, yes: bool) -> bool:
    if demo or yes or not sys.stdin.isatty():
        return True
    print(PERMISSION_GATE)
    try:
        answer = input("> ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "CONFIRM"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args_list = sys.argv[1:] if argv is None else list(argv)
    if not args_list or any(a in ("-h", "--help") for a in args_list):
        print(DISCLAIMER)
    args = parser.parse_args(args_list)

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

    if not _gate(list(args.targets), args.demo, args.yes):
        print("cancelled.", file=sys.stderr)
        return 2

    if not args.no_banner:
        print_banner()

    if args.demo:
        from thousand_eyes.demo import demo_transport
        transport = demo_transport()
    else:
        transport = None

    result = _scan_with_banner(
        dict(
            raw_seeds=list(args.targets),
            fold_plus=args.fold_plus,
            max_depth=max(0, args.depth),
            wild=args.wild,
            all_sites=args.all_sites or args.wild,
            transport=transport,
            url_log=args.url_log,
        ),
        no_banner=False,
    )

    text = render_text(result)
    if args.demo:
        text = "*** DEMO MODE — SYNTHETIC fixture replay, zero network calls ***\n\n" + text
    print(text)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(render_json(result) if not args.demo else render_json(result))
        print(f"\nfull report written to {args.json}")

    if args.dot:
        with open(args.dot, "w", encoding="utf-8") as fh:
            fh.write(render_dot(result))
        print(f"graph written to {args.dot}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
