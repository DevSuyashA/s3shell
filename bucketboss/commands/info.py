import collections

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
