# Security policy

## Scope

thousand-eyes is a read-only, fenced OSINT self-audit tool. The security
surface that matters:

- the fence itself (no credential probes, no broker APIs, no breach-dump
  pulls, no onion resolution: enforced in `agent/planner.py` + SafeClient)
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
