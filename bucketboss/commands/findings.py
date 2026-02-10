import json
import os
from datetime import datetime

from .recon import SEVERITY_ORDER, SEVERITY_DISPLAY


# Severity display mapping for findings table
_SEVERITY_SHORT = {
    'critical': '🔴 CRIT',
    'high':     '🟠 HIGH',
    'medium':   '🟡 MED',
    'info':     '🔵 INFO',
}

_SEVERITY_RANK = {sev: i for i, sev in enumerate(SEVERITY_ORDER)}


def _ensure_findings(app):
    """Initialize app.findings if it doesn't exist."""
    if getattr(app, 'findings', None) is None:
        app.findings = []


def _collect_findings(app):
    """Collect and merge findings from all sources into a unified sorted list.

    Each finding is a dict with keys:
        severity, source, path, summary
    """
    _ensure_findings(app)
    unified = []

    # From enum results
    enum_results = getattr(app, 'last_enum_results', None)
    if enum_results:
        classified = enum_results.get('classified', {})
        for severity in SEVERITY_ORDER:
            for item in classified.get(severity, []):
                full_key, _file_info, reason = item
                unified.append({
                    'severity': severity,
                    'source': 'enum',
                    'path': full_key,
                    'summary': reason,
                })

    # From TruffleHog results
    th_results = getattr(app, 'last_th_results', None)
    if th_results:
        for finding in th_results:
            detector = finding.get('detector', 'Unknown')
            verified = finding.get('verified', False)
            file_path = finding.get('file', 'unknown')
            verified_str = ' (VERIFIED ✅)' if verified else ''
            unified.append({
                'severity': 'critical',
                'source': 'th',
                'path': file_path,
                'summary': '%s%s' % (detector, verified_str),
            })

    # From manual tags
    for tag in app.findings:
        unified.append({
            'severity': tag.get('severity', 'info'),
            'source': 'tag',
            'path': tag['path'],
            'summary': '"%s"' % tag['note'],
        })

    # Sort by severity rank (critical first), then by source, then by path
    unified.sort(key=lambda f: (_SEVERITY_RANK.get(f['severity'], 99), f['source'], f['path']))

    return unified


def _get_bucket_url(app):
    """Build a display URL for the bucket."""
    prompt_prefix = app.provider.get_prompt_prefix()
    return prompt_prefix


def do_tag(app, *args):
    """Manually annotate a file with a note."""
    if len(args) < 2:
        print("Usage: tag <file> <note text>")
        return

    _ensure_findings(app)

    target = args[0]
    note = ' '.join(args[1:])

    # Resolve path relative to current prefix
    path = app.provider.resolve_path(app.current_prefix, target, is_directory=False)

    # Default severity for manual tags is info — user can override with --severity
    severity = 'info'

    # Check for --severity flag anywhere in the note args
    arg_list = list(args[1:])
    sev_idx = None
    for i, a in enumerate(arg_list):
        if a == '--severity' and i + 1 < len(arg_list):
            val = arg_list[i + 1].lower()
            if val in SEVERITY_ORDER:
                severity = val
                sev_idx = i
                break

    if sev_idx is not None:
        # Remove the --severity flag and value from note text
        del arg_list[sev_idx:sev_idx + 2]
        note = ' '.join(arg_list)

    tag_entry = {
        'path': path,
        'note': note,
        'severity': severity,
        'source': 'tag',
        'timestamp': datetime.now().isoformat(),
    }
    app.findings.append(tag_entry)

    print("✏️  Tagged: %s" % path)
    print("   Note: %s" % note)
    if severity != 'info':
        print("   Severity: %s" % _SEVERITY_SHORT.get(severity, severity))


def do_findings(app, *args):
    """View all accumulated findings."""
    _ensure_findings(app)

    # Parse arguments
    arg_list = list(args)
    severity_filter = None
    source_filter = None
    output_json = False

    i = 0
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--severity' and i + 1 < len(arg_list):
            val = arg_list[i + 1].lower()
            if val not in SEVERITY_ORDER:
                print("Invalid severity: %s (use critical|high|medium|info)" % val)
                return
            severity_filter = val
            i += 2
        elif arg == '--source' and i + 1 < len(arg_list):
            val = arg_list[i + 1].lower()
            if val not in ('enum', 'th', 'tag'):
                print("Invalid source: %s (use enum|th|tag)" % val)
                return
            source_filter = val
            i += 2
        elif arg == '--json':
            output_json = True
            i += 1
        elif arg == '--help':
            print("Usage: findings [--severity critical|high|medium|info] [--source enum|th|tag] [--json]")
            return
        else:
            print("Unknown option: %s" % arg)
            return

    findings = _collect_findings(app)

    # Apply severity filter (show at or above given level)
    if severity_filter:
        max_idx = SEVERITY_ORDER.index(severity_filter)
        allowed = set(SEVERITY_ORDER[:max_idx + 1])
        findings = [f for f in findings if f['severity'] in allowed]

    # Apply source filter
    if source_filter:
        findings = [f for f in findings if f['source'] == source_filter]

    if output_json:
        print(json.dumps(findings, indent=2))
        return

    bucket_url = _get_bucket_url(app)
    print("")
    print("📋 Findings for %s (%d items)" % (bucket_url, len(findings)))

    if not findings:
        print("   No findings yet. Run 'enum', 'th', or use 'tag' to annotate files.")
        print("")
        return

    # Calculate column widths
    max_path = max(len(f['path']) for f in findings)
    max_path = max(max_path, 4)  # minimum "Path" header width
    path_width = min(max_path, 40)

    print("")
    print(" %3s   %-10s %-6s  %-*s  %s" % ('#', 'Severity', 'Source', path_width, 'Path', 'Summary'))

    for idx, f in enumerate(findings, 1):
        sev_display = _SEVERITY_SHORT.get(f['severity'], f['severity'])
        path_display = f['path']
        if len(path_display) > path_width:
            path_display = '...' + path_display[-(path_width - 3):]
        print(" %3d   %-10s %-6s  %-*s  %s" % (
            idx, sev_display, f['source'], path_width, path_display, f['summary'],
        ))

    print("")


def do_export(app, *args):
    """Generate assessment report."""
    _ensure_findings(app)

    # Parse arguments
    arg_list = list(args)
    fmt = 'md'

    i = 0
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--format' and i + 1 < len(arg_list):
            val = arg_list[i + 1].lower()
            if val not in ('text', 'json', 'md'):
                print("Invalid format: %s (use text|json|md)" % val)
                return
            fmt = val
            i += 2
        elif arg == '--help':
            print("Usage: export [--format text|json|md]")
            return
        else:
            print("Unknown option: %s" % arg)
            return

    findings = _collect_findings(app)
    bucket_name = getattr(app.provider, 'bucket_name', 'unknown')
    date_str = datetime.now().strftime('%Y-%m-%d')

    ext_map = {'md': 'md', 'json': 'json', 'text': 'txt'}
    ext = ext_map[fmt]
    filename = 'bb-report-%s-%s.%s' % (bucket_name, date_str, ext)

    if fmt == 'json':
        content = _export_json(app, findings, bucket_name, date_str)
    elif fmt == 'md':
        content = _export_md(app, findings, bucket_name, date_str)
    else:
        content = _export_text(app, findings, bucket_name, date_str)

    with open(filename, 'w') as f:
        f.write(content)

    # Summary stats
    severity_counts = {}
    for f in findings:
        sev = f['severity']
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    tag_count = sum(1 for f in findings if f['source'] == 'tag')
    th_findings = [f for f in findings if f['source'] == 'th']
    th_verified = sum(1 for f in th_findings if 'VERIFIED' in f.get('summary', ''))

    print("")
    print("📄 Exported to: %s" % filename)
    print("   Findings:     %d" % len(findings))
    print("   Files tagged: %d" % tag_count)
    if th_findings:
        print("   TH secrets:   %d (%d verified)" % (len(th_findings), th_verified))
    enum_results = getattr(app, 'last_enum_results', None)
    if enum_results:
        print("   Enum scope:   {:,} objects across {:,} directories".format(
            enum_results.get('total_files', 0),
            enum_results.get('total_dirs', 0),
        ))
    print("")


def _export_json(app, findings, bucket_name, date_str):
    """Generate JSON export."""
    enum_results = getattr(app, 'last_enum_results', None)
    th_results = getattr(app, 'last_th_results', None)

    report = {
        'bucket': bucket_name,
        'date': date_str,
        'generated_at': datetime.now().isoformat(),
        'prompt_prefix': app.provider.get_prompt_prefix(),
        'findings': findings,
        'summary': {
            'total': len(findings),
            'by_severity': {},
            'by_source': {},
        },
    }

    for f in findings:
        sev = f['severity']
        report['summary']['by_severity'][sev] = report['summary']['by_severity'].get(sev, 0) + 1
        src = f['source']
        report['summary']['by_source'][src] = report['summary']['by_source'].get(src, 0) + 1

    if enum_results:
        report['enum_summary'] = {
            'prefix': enum_results.get('prefix', ''),
            'total_files': enum_results.get('total_files', 0),
            'total_size': enum_results.get('total_size', 0),
            'total_dirs': enum_results.get('total_dirs', 0),
            'timestamp': enum_results.get('timestamp', ''),
        }

    if th_results:
        report['trufflehog_results'] = []
        for r in th_results:
            report['trufflehog_results'].append({
                'detector': r.get('detector', 'Unknown'),
                'verified': r.get('verified', False),
                'file': r.get('file', ''),
            })

    return json.dumps(report, indent=2, default=str)


def _export_md(app, findings, bucket_name, date_str):
    """Generate Markdown export."""
    lines = []
    bucket_url = _get_bucket_url(app)

    lines.append("# BucketBoss Report: %s" % bucket_url)
    lines.append("")
    lines.append("**Date:** %s" % date_str)

    # Access info from stats if available
    stats = getattr(app, 'stats_result', {})
    if stats.get('status') == 'complete':
        lines.append("**Stats:** Collected")
    lines.append("")

    # Summary
    severity_counts = {}
    source_counts = {}
    for f in findings:
        sev = f['severity']
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        src = f['source']
        source_counts[src] = source_counts.get(src, 0) + 1

    lines.append("## Summary")
    lines.append("")
    lines.append("- Total findings: %d" % len(findings))

    sev_parts = []
    for sev in SEVERITY_ORDER:
        count = severity_counts.get(sev, 0)
        if count:
            _, label, _ = SEVERITY_DISPLAY[sev]
            sev_parts.append("%s: %d" % (label.capitalize(), count))
    if sev_parts:
        lines.append("- %s" % ', '.join(sev_parts))

    th_results = getattr(app, 'last_th_results', None)
    if th_results:
        verified = sum(1 for r in th_results if r.get('verified', False))
        lines.append("- TruffleHog secrets: %d (%d verified)" % (len(th_results), verified))

    enum_results = getattr(app, 'last_enum_results', None)
    if enum_results:
        lines.append("- Enum scope: {:,} objects across {:,} directories".format(
            enum_results.get('total_files', 0),
            enum_results.get('total_dirs', 0),
        ))

    lines.append("")

    # Findings by severity
    lines.append("## Findings")
    lines.append("")

    for sev in SEVERITY_ORDER:
        sev_findings = [f for f in findings if f['severity'] == sev]
        if not sev_findings:
            continue

        icon, label, _ = SEVERITY_DISPLAY[sev]
        lines.append("### %s %s (%d)" % (icon, label, len(sev_findings)))
        lines.append("")
        lines.append("| # | Source | Path | Details |")
        lines.append("|---|--------|------|---------|")

        for idx, f in enumerate(sev_findings, 1):
            lines.append("| %d | %s | `%s` | %s |" % (
                idx, f['source'], f['path'], f['summary'],
            ))

        lines.append("")

    # Enumeration summary
    if enum_results:
        lines.append("## Enumeration Summary")
        lines.append("")
        lines.append("- **Path:** `%s`" % (enum_results.get('prefix', '') or '/'))
        lines.append("- **Total objects:** {:,}".format(enum_results.get('total_files', 0)))
        lines.append("- **Total size:** %d bytes" % enum_results.get('total_size', 0))
        lines.append("- **Directories:** {:,}".format(enum_results.get('total_dirs', 0)))
        lines.append("- **Timestamp:** %s" % enum_results.get('timestamp', ''))
        lines.append("")

        classified = enum_results.get('classified', {})
        for sev in SEVERITY_ORDER:
            items = classified.get(sev, [])
            if items:
                _, label, _ = SEVERITY_DISPLAY[sev]
                lines.append("**%s:** %d files" % (label, len(items)))

        interesting_dirs = enum_results.get('interesting_dirs', [])
        if interesting_dirs:
            lines.append("")
            lines.append("**Interesting directories:**")
            for dirname, sev, reason in interesting_dirs:
                lines.append("- `%s/` — %s" % (dirname, reason))

        lines.append("")

    # TruffleHog detail
    if th_results:
        lines.append("## TruffleHog Results")
        lines.append("")
        lines.append("| # | Detector | File | Verified |")
        lines.append("|---|----------|------|----------|")
        for idx, r in enumerate(th_results, 1):
            verified_str = '✅ Yes' if r.get('verified', False) else '❓ No'
            lines.append("| %d | %s | `%s` | %s |" % (
                idx, r.get('detector', 'Unknown'), r.get('file', ''), verified_str,
            ))
        lines.append("")

    # Manual tags
    manual_tags = getattr(app, 'findings', [])
    if manual_tags:
        lines.append("## Manual Annotations")
        lines.append("")
        lines.append("| # | Path | Severity | Note | Timestamp |")
        lines.append("|---|------|----------|------|-----------|")
        for idx, tag in enumerate(manual_tags, 1):
            sev_display = _SEVERITY_SHORT.get(tag.get('severity', 'info'), tag.get('severity', 'info'))
            lines.append("| %d | `%s` | %s | %s | %s |" % (
                idx, tag['path'], sev_display, tag['note'], tag.get('timestamp', ''),
            ))
        lines.append("")

    lines.append("---")
    lines.append("*Generated by BucketBoss*")
    lines.append("")

    return "\n".join(lines)


def _export_text(app, findings, bucket_name, date_str):
    """Generate plain text export."""
    lines = []
    sep = '=' * 60
    thin_sep = '─' * 60
    bucket_url = _get_bucket_url(app)

    lines.append(sep)
    lines.append("  BucketBoss Report: %s" % bucket_url)
    lines.append("  Date: %s" % date_str)
    lines.append(sep)
    lines.append("")

    # Summary
    severity_counts = {}
    for f in findings:
        sev = f['severity']
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    lines.append("Summary: %d findings" % len(findings))
    sev_parts = []
    for sev in SEVERITY_ORDER:
        count = severity_counts.get(sev, 0)
        if count:
            _, label, _ = SEVERITY_DISPLAY[sev]
            sev_parts.append("%s: %d" % (label, count))
    if sev_parts:
        lines.append("  %s" % ', '.join(sev_parts))
    lines.append("")

    if not findings:
        lines.append("No findings.")
        return "\n".join(lines)

    # Calculate column widths
    max_path = max(len(f['path']) for f in findings)
    path_width = min(max(max_path, 4), 40)

    lines.append(thin_sep)
    lines.append(" %3s   %-10s %-6s  %-*s  %s" % ('#', 'Severity', 'Source', path_width, 'Path', 'Summary'))
    lines.append(thin_sep)

    for idx, f in enumerate(findings, 1):
        sev_display = _SEVERITY_SHORT.get(f['severity'], f['severity'])
        path_display = f['path']
        if len(path_display) > path_width:
            path_display = '...' + path_display[-(path_width - 3):]
        lines.append(" %3d   %-10s %-6s  %-*s  %s" % (
            idx, sev_display, f['source'], path_width, path_display, f['summary'],
        ))

    lines.append(thin_sep)
    lines.append("")
    lines.append("Generated by BucketBoss")
    lines.append("")

    return "\n".join(lines)
