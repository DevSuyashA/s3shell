import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from fnmatch import fnmatch

from ..formatting import human_readable_size


# Severity levels in priority order
SEVERITY_ORDER = ['critical', 'high', 'medium', 'info']

SEVERITY_DISPLAY = {
    'critical': ('\U0001f534', 'CRITICAL', 'likely secrets, inspect immediately'),
    'high':     ('\U0001f7e0', 'HIGH', 'config & infra, often contains embedded secrets'),
    'medium':   ('\U0001f7e1', 'MEDIUM', 'worth a closer look'),
    'info':     ('\U0001f535', 'INFO', 'media, assets, dependencies'),
}

DIR_SEVERITY_ICONS = {
    'critical': '\U0001f534',
    'high': '\U0001f7e0',
    'medium': '\U0001f7e1',
}

MAX_DISPLAY_PER_SEVERITY = 10


def _load_rules():
    """Load classification rules from enum_rules.json."""
    rules_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'enum_rules.json')
    with open(rules_path, 'r') as f:
        return json.load(f)


def _classify_file(basename, full_path, rules):
    """Classify a single file against rules. Returns (severity, reason)."""
    basename_lower = basename.lower()
    full_path_lower = full_path.lower()

    for severity in SEVERITY_ORDER:
        for rule in rules['files'].get(severity, []):
            match_type = rule.get('type', 'exact')
            pattern = rule['pattern']

            if match_type == 'exact':
                if basename_lower == pattern.lower():
                    return severity, rule['reason']
            elif match_type == 'glob':
                if fnmatch(basename_lower, pattern.lower()):
                    return severity, rule['reason']
            elif match_type == 'path_glob':
                if fnmatch(basename_lower, pattern.lower()) or fnmatch(full_path_lower, pattern.lower()):
                    return severity, rule['reason']

    return 'info', 'Unclassified file'


def _classify_directory(dirname, rules):
    """Classify a directory name. Returns (severity, reason) or None."""
    dirname_lower = dirname.lower().rstrip('/')

    for severity in ['critical', 'high', 'medium']:
        for rule in rules['directories'].get(severity, []):
            if dirname_lower == rule['pattern'].lower():
                return severity, rule['reason']

    return None


def _parse_enum_args(args):
    """Parse enum command arguments."""
    arg_list = list(args)
    path = ''
    depth = 5
    download = False
    min_severity = 'info'
    classify = True

    i = 0
    positional_consumed = False
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--depth' and i + 1 < len(arg_list):
            try:
                depth = int(arg_list[i + 1])
            except ValueError:
                print("Invalid depth: " + arg_list[i + 1])
                return None
            i += 2
        elif arg == '--download':
            download = True
            i += 1
        elif arg == '--min-severity' and i + 1 < len(arg_list):
            val = arg_list[i + 1].lower()
            if val not in SEVERITY_ORDER:
                print("Invalid severity: " + val + " (use critical|high|medium|info)")
                return None
            min_severity = val
            i += 2
        elif arg == '--no-classify':
            classify = False
            i += 1
        elif arg == '--help':
            print("Usage: enum [path] [--depth N] [--download] [--min-severity critical|high|medium|info] [--no-classify]")
            return None
        elif not arg.startswith('-') and not positional_consumed:
            path = arg
            positional_consumed = True
            i += 1
        else:
            print("Unknown option: " + arg)
            return None

    return {
        'path': path,
        'depth': depth,
        'download': download,
        'min_severity': min_severity,
        'classify': classify,
    }


def _recursive_list(app, prefix, max_depth, current_depth=0):
    """Recursively list all objects under prefix up to max_depth.
    Returns (all_files, all_dirs_seen) where all_files is list of
    (full_key, file_info) and all_dirs_seen is list of (dir_name, full_prefix).
    """
    all_files = []
    all_dirs = []

    dirs, files, _ = app.list_objects(prefix)

    for f in files:
        full_key = prefix + f['name']
        all_files.append((full_key, f))

    if current_depth < max_depth:
        for d in dirs:
            dir_prefix = prefix + d + '/'
            all_dirs.append((d, dir_prefix))
            sub_files, sub_dirs = _recursive_list(app, dir_prefix, max_depth, current_depth + 1)
            all_files.extend(sub_files)
            all_dirs.extend(sub_dirs)

    return all_files, all_dirs


def _format_date(f):
    """Extract a YYYY-MM-DD date string from a file_info dict."""
    lm = f.get('last_modified')
    if not lm:
        return ''
    if isinstance(lm, datetime):
        return lm.strftime('%Y-%m-%d')
    return str(lm)[:10]


def do_enum(app, *args):
    """Enumerate and classify objects in the bucket."""
    opts = _parse_enum_args(args)
    if opts is None:
        return

    rules = _load_rules()

    # Resolve target path
    target_path = opts['path']
    if target_path:
        prefix = app.provider.resolve_path(app.current_prefix, target_path, is_directory=True)
    else:
        prefix = app.current_prefix

    bucket_name = getattr(app.provider, 'bucket_name', 'bucket')
    display_path = prefix if prefix else bucket_name + '/'

    print("")
    print("\U0001f50d Enumerating %s (depth: %d)..." % (display_path, opts['depth']))

    # Recursive crawl
    all_files, all_dirs = _recursive_list(app, prefix, opts['depth'])

    dir_set = set()
    for _, dir_prefix in all_dirs:
        dir_set.add(dir_prefix)

    print("   Scanned: {:,} objects across {:,} directories".format(len(all_files), len(dir_set)))

    if not opts['classify']:
        # Raw enumeration — just list everything
        for full_key, f in all_files:
            size_str = human_readable_size(f.get('size', 0))
            date_str = _format_date(f)
            print("   %-55s %9s   %s" % (full_key, size_str, date_str))
        print()
        return

    # Classify files
    classified = defaultdict(list)  # severity -> [(full_key, file_info, reason)]
    total_size = 0

    for full_key, f in all_files:
        basename = f['name']
        severity, reason = _classify_file(basename, full_key, rules)
        classified[severity].append((full_key, f, reason))
        total_size += f.get('size', 0)

    # Classify directories
    interesting_dirs = []  # (dir_name, severity, reason)
    for dirname, dir_prefix in all_dirs:
        result = _classify_directory(dirname, rules)
        if result:
            sev, reason = result
            interesting_dirs.append((dirname, sev, reason))

    # Filter by min severity
    min_idx = SEVERITY_ORDER.index(opts['min_severity'])
    display_severities = SEVERITY_ORDER[:min_idx + 1]

    # Display tiered output
    for severity in SEVERITY_ORDER:
        if severity not in display_severities:
            continue

        files_list = classified.get(severity, [])
        if not files_list:
            continue

        icon, label, desc = SEVERITY_DISPLAY[severity]
        print("")
        print("%s %s (%d files) \u2014 %s" % (icon, label, len(files_list), desc))

        if severity == 'info':
            print("   (Omitted \u2014 use 'enum report --format json' for complete inventory)")
            continue

        # Show up to MAX_DISPLAY_PER_SEVERITY files
        for full_key, f, reason in files_list[:MAX_DISPLAY_PER_SEVERITY]:
            size_str = human_readable_size(f.get('size', 0))
            date_str = _format_date(f)
            print("   %-50s %9s   %s" % (full_key, size_str, date_str))

        remaining = len(files_list) - MAX_DISPLAY_PER_SEVERITY
        if remaining > 0:
            print("   ...and %d more (use 'enum report' for full list)" % remaining)

    # Summary
    print("")
    print("\U0001f4ca Summary:")
    print("   Total: {:,} objects | {}".format(len(all_files), human_readable_size(total_size)))

    if interesting_dirs:
        dir_parts = []
        for dirname, sev, _ in interesting_dirs:
            icon = DIR_SEVERITY_ICONS.get(sev, '')
            dir_parts.append("%s/ (%s)" % (dirname, icon))
        print("   Directories with interesting names: " + ', '.join(dir_parts))

    # Suggested next steps based on critical findings
    critical_files = classified.get('critical', [])
    high_files = classified.get('high', [])

    suggestions = []
    for full_key, f, reason in critical_files[:3]:
        suggestions.append("cat " + full_key)
    for full_key, f, reason in high_files[:1]:
        suggestions.append("peek " + full_key)

    if suggestions:
        print("")
        print("\U0001f4a1 Suggested next steps:")
        for s in suggestions:
            print("   " + s)

    print()

    # Store results for later use
    app.last_enum_results = {
        'prefix': prefix,
        'total_files': len(all_files),
        'total_size': total_size,
        'total_dirs': len(dir_set),
        'classified': {sev: [(k, f, r) for k, f, r in items] for sev, items in classified.items()},
        'interesting_dirs': interesting_dirs,
        'timestamp': datetime.now().isoformat(),
    }

    # Auto-download critical files if --download
    if opts['download'] and critical_files:
        download_dir = "./bb-enum-" + bucket_name
        os.makedirs(download_dir, exist_ok=True)
        print("\u2b07\ufe0f  Downloading %d CRITICAL files to %s/..." % (len(critical_files), download_dir))
        for full_key, f, reason in critical_files:
            local_path = os.path.join(download_dir, full_key.replace('/', os.sep))
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)
            try:
                app.provider.download_file(full_key, local_path)
                print("   \u2705 " + full_key)
            except Exception as e:
                print("   \u274c %s: %s" % (full_key, e))
        print()


def _parse_report_args(args):
    """Parse enum report arguments."""
    arg_list = list(args)
    fmt = 'text'

    i = 0
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--format' and i + 1 < len(arg_list):
            val = arg_list[i + 1].lower()
            if val not in ('text', 'json', 'md'):
                print("Invalid format: %s (use text|json|md)" % val)
                return None
            fmt = val
            i += 2
        elif arg == '--help':
            print("Usage: enum report [--format text|json|md]")
            return None
        else:
            i += 1

    return {'format': fmt}


def do_enum_report(app, *args):
    """Output the full results from the last enum run."""
    results = getattr(app, 'last_enum_results', None)
    if not results:
        print("No enum results available. Run 'enum' first.")
        return

    opts = _parse_report_args(args)
    if opts is None:
        return

    fmt = opts['format']

    if fmt == 'json':
        # Serialize — file_info dicts may have datetime objects
        output = {
            'prefix': results['prefix'],
            'total_files': results['total_files'],
            'total_size': results['total_size'],
            'total_dirs': results['total_dirs'],
            'timestamp': results['timestamp'],
            'interesting_dirs': [
                {'name': d, 'severity': s, 'reason': r}
                for d, s, r in results['interesting_dirs']
            ],
            'classified': {},
        }
        for sev, items in results['classified'].items():
            output['classified'][sev] = []
            for full_key, f, reason in items:
                entry = {
                    'key': full_key,
                    'size': f.get('size', 0),
                    'reason': reason,
                }
                lm = f.get('last_modified')
                if lm:
                    if isinstance(lm, datetime):
                        entry['last_modified'] = lm.isoformat()
                    else:
                        entry['last_modified'] = str(lm)
                output['classified'][sev].append(entry)

        print(json.dumps(output, indent=2))
        return

    if fmt == 'md':
        _render_report_md(results)
        return

    # Default: text
    _render_report_text(results)


def _render_report_text(results):
    """Render full text report."""
    sep = '=' * 60
    thin_sep = '\u2500' * 60
    print("")
    print(sep)
    print("  BucketBoss Enum Report")
    print("  Path: " + (results['prefix'] or '/'))
    print("  Time: " + results['timestamp'])
    print("  Total: {:,} objects | {}".format(results['total_files'], human_readable_size(results['total_size'])))
    print(sep)

    for severity in SEVERITY_ORDER:
        items = results['classified'].get(severity, [])
        if not items:
            continue

        icon, label, desc = SEVERITY_DISPLAY[severity]
        print("")
        print("%s %s (%d files) \u2014 %s" % (icon, label, len(items), desc))
        print(thin_sep)

        for full_key, f, reason in items:
            size_str = human_readable_size(f.get('size', 0))
            date_str = _format_date(f)
            print("   %-50s %9s   %s" % (full_key, size_str, date_str))
            print("      \u2514\u2500 " + reason)

    if results['interesting_dirs']:
        print("")
        print("\U0001f4c1 Interesting Directories")
        print(thin_sep)
        for dirname, sev, reason in results['interesting_dirs']:
            icon = DIR_SEVERITY_ICONS.get(sev, '')
            print("   %s %s/ \u2014 %s" % (icon, dirname, reason))

    print()


def _render_report_md(results):
    """Render markdown report."""
    print("# BucketBoss Enum Report")
    print("")
    print("- **Path:** `%s`" % (results['prefix'] or '/'))
    print("- **Time:** " + results['timestamp'])
    print("- **Total:** {:,} objects | {}".format(results['total_files'], human_readable_size(results['total_size'])))
    print()

    for severity in SEVERITY_ORDER:
        items = results['classified'].get(severity, [])
        if not items:
            continue

        icon, label, desc = SEVERITY_DISPLAY[severity]
        print("## %s %s (%d files) \u2014 %s" % (icon, label, len(items), desc))
        print("")
        print("| File | Size | Modified | Reason |")
        print("|------|------|----------|--------|")

        for full_key, f, reason in items:
            size_str = human_readable_size(f.get('size', 0))
            date_str = _format_date(f)
            print("| `%s` | %s | %s | %s |" % (full_key, size_str, date_str, reason))

        print()

    if results['interesting_dirs']:
        print("## \U0001f4c1 Interesting Directories")
        print("")
        print("| Directory | Severity | Reason |")
        print("|-----------|----------|--------|")
        for dirname, sev, reason in results['interesting_dirs']:
            icon = DIR_SEVERITY_ICONS.get(sev, '')
            _, label, _ = SEVERITY_DISPLAY[sev]
            print("| `%s/` | %s %s | %s |" % (dirname, icon, label, reason))
        print()


# ---------------------------------------------------------------------------
# th — TruffleHog integration
# ---------------------------------------------------------------------------

def _parse_th_args(args):
    """Parse th command arguments."""
    arg_list = list(args)
    target = ''
    depth = 3
    verified_only = False
    output_json = False
    keep = False
    max_size = 50 * 1024 * 1024  # 50 MB

    i = 0
    positional_consumed = False
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--depth' and i + 1 < len(arg_list):
            try:
                depth = int(arg_list[i + 1])
            except ValueError:
                print("Invalid depth: " + arg_list[i + 1])
                return None
            i += 2
        elif arg == '--verified-only':
            verified_only = True
            i += 1
        elif arg == '--json':
            output_json = True
            i += 1
        elif arg == '--keep':
            keep = True
            i += 1
        elif arg == '--max-size' and i + 1 < len(arg_list):
            try:
                max_size = int(arg_list[i + 1])
            except ValueError:
                print("Invalid max-size: " + arg_list[i + 1])
                return None
            i += 2
        elif arg == '--help':
            print("Usage: th <file_or_dir> [--depth N] [--verified-only] [--json] [--keep] [--max-size SIZE]")
            print("       th status")
            return None
        elif not arg.startswith('-') and not positional_consumed:
            target = arg
            positional_consumed = True
            i += 1
        else:
            print("Unknown option: " + arg)
            return None

    return {
        'target': target,
        'depth': depth,
        'verified_only': verified_only,
        'json': output_json,
        'keep': keep,
        'max_size': max_size,
    }


def _redact_secret(raw, length=12):
    """Truncate/redact a secret string for display."""
    if not raw:
        return '(empty)'
    s = str(raw)
    if len(s) <= length:
        return s[:4] + '...'
    return s[:length] + '...'


def do_th(app, *args):
    """Run TruffleHog against remote files to find secrets."""
    if args and args[0] == 'status':
        th_path = shutil.which('trufflehog')
        if th_path:
            print("✅ trufflehog found: " + th_path)
            try:
                result = subprocess.run(
                    ['trufflehog', '--version'], capture_output=True, text=True, timeout=10,
                )
                version_out = (result.stdout.strip() or result.stderr.strip())
                if version_out:
                    print("   Version: " + version_out)
            except Exception:
                pass
        else:
            print("❌ trufflehog not found in PATH")
            print("   Install: https://github.com/trufflesecurity/trufflehog#installation")
        return

    opts = _parse_th_args(args)
    if opts is None:
        return

    if not opts['target']:
        print("Usage: th <file_or_dir> [--depth N] [--verified-only] [--json] [--keep] [--max-size SIZE]")
        return

    # Check trufflehog availability
    if not shutil.which('trufflehog'):
        print("❌ trufflehog not found in PATH. Run 'th status' for install info.")
        return

    target = opts['target']
    is_dir = target.endswith('/')

    # Resolve path
    if is_dir:
        prefix = app.provider.resolve_path(app.current_prefix, target, is_directory=True)
    else:
        file_key = app.provider.resolve_path(app.current_prefix, target, is_directory=False)

    temp_dir = tempfile.mkdtemp(prefix='bb-th-')
    local_to_remote = {}  # local_path -> remote_key

    try:
        if is_dir:
            print("🔍 Scanning directory: %s (depth: %d)" % (prefix, opts['depth']))
            all_files, _ = _recursive_list(app, prefix, opts['depth'])
            if not all_files:
                print("   No files found.")
                return

            downloaded = 0
            skipped = 0
            for full_key, f in all_files:
                if f.get('size', 0) > opts['max_size']:
                    skipped += 1
                    continue
                # Preserve directory structure in temp dir
                rel_path = full_key
                local_path = os.path.join(temp_dir, rel_path.replace('/', os.sep))
                local_dir = os.path.dirname(local_path)
                if local_dir:
                    os.makedirs(local_dir, exist_ok=True)
                try:
                    app.provider.download_file(full_key, local_path)
                    local_to_remote[local_path] = full_key
                    downloaded += 1
                    sys.stdout.write("\r   Downloaded: %d / %d" % (downloaded, len(all_files) - skipped))
                    sys.stdout.flush()
                except Exception as e:
                    print("\n   ⚠ Failed to download %s: %s" % (full_key, e), file=sys.stderr)

            if downloaded == 0:
                print("\n   No files downloaded.")
                return
            print("\n   Downloaded %d files (%d skipped over max-size)" % (downloaded, skipped))
        else:
            # Single file
            try:
                meta = app.provider.get_object_metadata(file_key)
                if meta.get('size', 0) > opts['max_size']:
                    print("⚠ File too large (%s). Use --max-size to override." % human_readable_size(meta['size']))
                    return
            except Exception:
                pass  # proceed anyway, download will fail if not readable

            local_path = os.path.join(temp_dir, os.path.basename(file_key))
            try:
                app.provider.download_file(file_key, local_path)
                local_to_remote[local_path] = file_key
                print("🔍 Scanning: %s" % file_key)
            except Exception as e:
                print("❌ Failed to download %s: %s" % (file_key, e))
                return

        # Run TruffleHog
        print("   Running trufflehog...")
        try:
            result = subprocess.run(
                ['trufflehog', 'filesystem', '--directory', temp_dir, '--json'],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            print("❌ TruffleHog timed out after 5 minutes.")
            return
        except Exception as e:
            print("❌ Failed to run trufflehog: %s" % e)
            return

        # Parse findings
        findings = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                finding = json.loads(line)
                findings.append(finding)
            except json.JSONDecodeError:
                continue

        if opts['verified_only']:
            findings = [f for f in findings if f.get('Verified')]

        # Map findings back to remote paths
        processed = []
        for finding in findings:
            source_meta = finding.get('SourceMetadata', {})
            data = source_meta.get('Data', {})
            filesystem = data.get('Filesystem', {})
            local_file = filesystem.get('file', '')

            # Map local path to remote key
            remote_key = local_file
            for lp, rk in local_to_remote.items():
                if local_file and (local_file == lp or local_file.endswith(os.sep + os.path.relpath(lp, temp_dir))):
                    remote_key = rk
                    break
            else:
                # Try to extract from the temp dir path
                if local_file.startswith(temp_dir):
                    rel = local_file[len(temp_dir):].lstrip(os.sep).replace(os.sep, '/')
                    remote_key = rel

            processed.append({
                'detector': finding.get('DetectorName', 'Unknown'),
                'verified': finding.get('Verified', False),
                'file': remote_key,
                'raw': finding.get('Raw', ''),
                'source': finding,
            })

        # Display results
        if opts['json']:
            print(json.dumps(processed, indent=2, default=str))
        elif not processed:
            print("\n✅ No secrets found.")
        else:
            print("\n🔑 Found %d secret(s):\n" % len(processed))
            for i, p in enumerate(processed, 1):
                verified_icon = '✅' if p['verified'] else '❓'
                print("   %d. %s [%s] %s" % (i, verified_icon, p['detector'], p['file']))
                print("      Secret: %s" % _redact_secret(p['raw']))

            verified_count = sum(1 for p in processed if p['verified'])
            if verified_count:
                print("\n   ⚠ %d verified secret(s) found!" % verified_count)

        # Store results
        app.last_th_results = processed

        if opts['keep']:
            print("\n   📁 Temp files kept at: %s" % temp_dir)

    finally:
        if not opts.get('keep', False):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# scope — Bucket scope overview
# ---------------------------------------------------------------------------

def do_scope(app, *args):
    """Scan the bucket/prefix and show a scope summary."""
    arg_list = list(args)
    path = ''
    if arg_list and not arg_list[0].startswith('-'):
        path = arg_list[0]

    if path:
        prefix = app.provider.resolve_path(app.current_prefix, path, is_directory=True)
    else:
        prefix = app.current_prefix

    print("")
    print("🔭 Scanning bucket...")

    total_objects = 0
    total_size = 0
    ext_counter = Counter()
    prefix_counter = Counter()
    deepest_path = ''
    deepest_depth = 0
    oldest_date = None
    oldest_key = ''
    newest_date = None
    newest_key = ''

    def _walk(pfx, depth):
        nonlocal total_objects, total_size, deepest_path, deepest_depth
        nonlocal oldest_date, oldest_key, newest_date, newest_key

        dirs, files, _ = app.list_objects(pfx)

        for f in files:
            total_objects += 1
            total_size += f.get('size', 0)

            ext = f.get('extension', '')
            if ext:
                ext_counter[ext] += 1
            else:
                ext_counter['(none)'] += 1

            full_key = pfx + f['name']

            # Track top-level prefix
            if pfx:
                top_prefix = pfx.split('/')[0] + '/'
                prefix_counter[top_prefix] += 1
            else:
                prefix_counter['(root)'] += 1

            # Track depth
            key_depth = full_key.count('/')
            if key_depth > deepest_depth:
                deepest_depth = key_depth
                parent = full_key.rsplit('/', 1)[0] + '/' if '/' in full_key else '(root)'
                deepest_path = parent

            # Track dates
            lm = f.get('last_modified')
            if lm:
                if isinstance(lm, datetime):
                    if oldest_date is None or lm < oldest_date:
                        oldest_date = lm
                        oldest_key = full_key
                    if newest_date is None or lm > newest_date:
                        newest_date = lm
                        newest_key = full_key

            if total_objects % 100 == 0:
                sys.stdout.write("\r   Scanned: {:,} objects...".format(total_objects))
                sys.stdout.flush()

        for d in dirs:
            sub_prefix = pfx + d + '/'
            _walk(sub_prefix, depth + 1)

    _walk(prefix, 0)

    # Clear progress line
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    # Print results
    print("📊 Scope Results:")
    print("   Total objects:  {:,}".format(total_objects))
    print("   Total size:     %s" % human_readable_size(total_size))

    if deepest_path:
        print("   Deepest path:   %s (%d levels)" % (deepest_path, deepest_depth))

    if ext_counter:
        top_exts = ext_counter.most_common(5)
        parts = ["%s (%s)" % (ext, "{:,}".format(count)) for ext, count in top_exts]
        print("   Top extensions: " + '  '.join(parts))

    if prefix_counter:
        top_pfx = prefix_counter.most_common(5)
        parts = ["%s (%s)" % (p, "{:,}".format(count)) for p, count in top_pfx]
        print("   Top prefixes:   " + '  '.join(parts))

    if oldest_date:
        print("   Oldest object:  %s (%s)" % (oldest_date.strftime('%Y-%m-%d'), oldest_key))
    if newest_date:
        print("   Newest object:  %s (%s)" % (newest_date.strftime('%Y-%m-%d'), newest_key))

    print()