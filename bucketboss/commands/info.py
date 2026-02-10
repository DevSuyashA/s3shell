import collections
import os
import sys
from datetime import datetime

from ..formatting import human_readable_size


def do_stats(app, *args):
    """Display collected bucket statistics and cached content summary."""

    # --- Provider-Specific Stats (from background thread) ---
    provider_status = app.stats_result.get("status", "unknown")
    print("--- Provider Bucket Stats ---")
    if provider_status == "pending" or provider_status == "loading":
        print("  Status: Collection in progress...")
    elif provider_status == "error":
        print(f"  Status: Error collecting provider stats - {app.stats_result.get('error_message', 'Unknown error')}")
    elif provider_status == "complete":
        print("  Status: Complete (collected in background)")
        for key, value in app.stats_result.items():
            if key not in ["status", "error_message"]:
                print(f"  {key}: {value}")
    else:
        print(f"  Status: Unknown ({provider_status})")

    # --- Cached Content Stats ---
    print("\n--- Cached Content Stats (reflects browsed/crawled data) ---")
    if not app.cache:
        print("  Cache is empty. Browse directories to populate.")
        return

    cached_dirs = set()
    file_type_counts = collections.Counter()
    total_cached_files = 0
    total_cached_size_bytes = 0

    for prefix, (dirs, files, timestamp) in app.cache.items():
        if prefix == '' or prefix.endswith('/'):
            cached_dirs.add(prefix)

        for d_name in dirs:
            cached_dirs.add(prefix + d_name + '/')

        for f_info in files:
            ext = f_info.get('extension', '.<no_ext>')
            if not ext:
                ext = '.<no_ext>'
            file_type_counts[ext] += 1
            total_cached_files += 1
            total_cached_size_bytes += f_info.get('size', 0)

    print(f"  Unique Cached Directories: {len(cached_dirs)}")
    print(f"  Total Cached Files: {total_cached_files}")
    print(f"  Total Cached Files Size: {human_readable_size(total_cached_size_bytes)}")

    if file_type_counts:
        print("  File Types (by extension):")
        sorted_file_types = sorted(file_type_counts.items(), key=lambda item: (-item[1], item[0]))
        for ext, count in sorted_file_types:
            print(f"    {ext if ext else '<no_extension>'}: {count}")
    else:
        print("  File Types (by extension): No files found in cache.")


def do_crawl_status(app, *args):
    """Display the status of the background cache crawl."""
    status = app.crawl_status.get("status", "unknown")
    depth = app.crawl_status.get("depth", 0)
    cached = app.crawl_status.get("cached_prefixes", 0)

    if status == "pending":
        print("Background cache crawl has not started yet.")
    elif status == "loading":
        print(f"Background cache crawl in progress... (Current Depth: {depth}, Prefixes Cached: {cached})")
    elif status == "complete":
        print(f"Background cache crawl complete. (Max Depth: {depth}, Prefixes Cached: {cached})")
    elif status == "error":
        print(f"Background cache crawl finished with an error: {app.crawl_status.get('error_message', 'Unknown error')}")
    else:
        print(f"Crawl status unknown: {status}")


def do_audit(app, *args):
    """Audit bucket permissions (ACLs, Policy, Public Access)."""
    print("Audit not yet implemented in provider. (Placeholder)")


def do_pwd(app, *args):
    """Print the full current remote path."""
    print(app.provider.get_prompt_prefix() + app.current_prefix)


def do_info(app, *args):
    """Show full metadata for a file."""
    if not args:
        print("Usage: info <file>")
        return

    target = args[0]
    key = app.provider.resolve_path(app.current_prefix, target, is_directory=False)

    try:
        meta = app.provider.get_object_metadata(key)
    except Exception as e:
        print("Error: %s" % e)
        return

    size = meta.get('size', 0)
    last_modified = meta.get('last_modified')
    content_type = meta.get('content_type', 'unknown')

    if isinstance(last_modified, datetime):
        date_str = last_modified.strftime('%Y-%m-%d %H:%M:%S %Z')
    elif last_modified:
        date_str = str(last_modified)
    else:
        date_str = 'unknown'

    print("")
    print("📄 %s" % key)
    print("   Size:          %s (%s bytes)" % (human_readable_size(size), "{:,}".format(size)))
    print("   Last modified: %s" % date_str)
    print("   Content-Type:  %s" % content_type)
    print("   Full key:      %s" % key)
    print("")


def do_head(app, *args):
    """Show first N lines of a file."""
    if not args:
        print("Usage: head <file> [lines]")
        return

    target = args[0]
    num_lines = 10
    if len(args) > 1:
        try:
            num_lines = int(args[1])
        except ValueError:
            print("Invalid line count: %s" % args[1])
            return

    key = app.provider.resolve_path(app.current_prefix, target, is_directory=False)

    try:
        # Read first ~64KB to get the head lines
        data = app.provider.read_object_range(key, 65536)
    except Exception:
        # Fall back to full object download if range requests fail
        try:
            data = app.provider.get_object(key)
        except Exception as e:
            print("Error: %s" % e)
            return

    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        print("⚠ Binary file detected. Use 'peek %s' instead." % target)
        return

    lines = text.split('\n')
    for line in lines[:num_lines]:
        print(line)


# ---------------------------------------------------------------------------
# du — disk usage summary
# ---------------------------------------------------------------------------

def do_du(app, *args):
    """Disk usage summary for remote directories.

    Usage: du [path] [--depth N]
    Options:
      --depth N   Depth to report (default: 1, summarizes immediate children)
    """
    from ..parallel import parallel_walk, get_workers_from_app

    arg_list = list(args)
    path = None
    depth = 1

    i = 0
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--depth' and i + 1 < len(arg_list):
            try:
                depth = int(arg_list[i + 1])
            except ValueError:
                print("Invalid depth: " + arg_list[i + 1])
                return
            i += 2
        elif arg == '--help':
            print("Usage: du [path] [--depth N]")
            return
        elif not arg.startswith('-') and path is None:
            path = arg
            i += 1
        else:
            print("Unknown option: " + arg)
            return

    # Resolve prefix
    if path:
        prefix = app.provider.resolve_path(app.current_prefix, path, is_directory=True)
    else:
        prefix = app.current_prefix

    print()
    print("📊 Disk usage: %s" % (prefix or '/'))
    print()

    workers = get_workers_from_app(app)

    # First list the current level to get immediate children
    dirs, files, _ = app.list_objects(prefix)

    entries = []  # (name, size)

    # Size of files at the current level (not in subdirectories)
    root_file_size = sum(f.get('size', 0) for f in files)
    if root_file_size > 0:
        entries.append(('.', root_file_size))

    # Walk all subdirectories in parallel, then aggregate per top-level child
    if dirs:
        sys.stdout.write("   Scanning...\r")
        sys.stdout.flush()

        # Collect all prefixes to walk in parallel
        sub_prefixes = [(prefix + d + '/', d) for d in dirs]

        # Walk each subdirectory — use parallel_walk for each
        # Submit all subdirectory walks at once via parallel_walk on the parent
        all_files, _, _ = parallel_walk(app, prefix, max_depth=50, workers=workers)

        # Aggregate sizes per immediate child directory
        dir_sizes = collections.Counter()
        for full_key, f in all_files:
            rel = full_key[len(prefix):] if full_key.startswith(prefix) else full_key
            if '/' in rel:
                top_dir = rel.split('/')[0] + '/'
                dir_sizes[top_dir] += f.get('size', 0)
            # Files at root level are already counted in root_file_size

        for d in dirs:
            dname = d + '/'
            entries.append((dname, dir_sizes.get(dname, 0)))

        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()

    # Sort by size descending
    entries.sort(key=lambda x: x[1], reverse=True)

    total = sum(size for _, size in entries)

    for name, size in entries:
        print("  %9s  %s" % (human_readable_size(size), name))

    print("  %9s  total" % human_readable_size(total))
    print()
