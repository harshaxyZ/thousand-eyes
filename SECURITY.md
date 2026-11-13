# Security policy

## Scope

thousand-eyes is a read-only, fenced OSINT self-audit tool. The security
surface that matters:

- the fence itself (no credential probes, no broker APIs, no breach-dump
  pulls: enforced in `agent/planner.py` + SafeClient). The agent fence
  refuses .onion hosts outright. The default engine contacts no onion
  service; `--tor` opts into one specific case only: querying Ahmia's onion
  search index for index MENTIONS. Even then the tool never fetches a hidden
  service's content and never pulls a dump - a mention is cited, not visited.
- network transparency (`--url-log` must capture every outbound request)
- report rendering (HTML dossier must stay zero-external-asset: no CDN,
  no remote fonts, no scripts phoning home)

## Reporting

Open a private security advisory via GitHub (Security > Advisories > New)
or email the owner. Please include: the module or fence path involved, a
minimal reproduction, and the expected vs actual behavior.

Do not open public issues for anything that weakens the fence.

## What we will not do

No CVE-chasing theater. Reports that ask the tool to scan identifiers you
do not own are rejected by design; that is not a bug, that is the point.
