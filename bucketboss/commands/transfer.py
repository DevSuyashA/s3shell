import difflib
import fnmatch
import hashlib
import os
import re
import shutil
import sys
import tempfile

from botocore.exceptions import ClientError

from ..formatting import human_readable_size


def do_get(app, *args):
    """Download a remote file to local directory where BucketBoss was started."""
    if len(args) < 1 or len(args) > 2:
        print("Usage: get <remote_path> [<local_path>]")
        return
    remote_path_arg = args[0]
    local_dest_arg = args[1] if len(args) == 2 else None

    # Wildcard pattern support
    if any(ch in remote_path_arg for ch in ['*', '?']):
        if '/' in remote_path_arg:
            dir_part, pattern = remote_path_arg.rsplit('/', 1)
            dir_part += '/'
        else:
            dir_part = ''
            pattern = remote_path_arg
        prefix = app.provider.resolve_path(app.current_prefix, dir_part, is_directory=True)
        _, files, _ = app.list_objects(prefix)
        names = [f['name'] for f in files]
        matches = fnmatch.filter(names, pattern)
        if not matches:
            print(f"No matches for pattern: {remote_path_arg}")
            return

        from ..parallel import parallel_download, get_workers_from_app

        # Build file_keys list with sizes
        file_size_map = {f['name']: f.get('size', 0) for f in files}
        file_keys = [(prefix + name, file_size_map.get(name, 0)) for name in matches]

        local_base = local_dest_arg if local_dest_arg else os.getcwd()

        def _progress(completed, total, current_file):
            sys.stdout.write("\r   Downloaded: %d / %d" % (completed, total))
            sys.stdout.flush()

        workers = get_workers_from_app(app)
        downloaded, skipped, errors = parallel_download(
            app, file_keys, local_base, workers=workers, flat=True,
            progress_callback=_progress,
        )

        if file_keys:
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()

        for rk, lp in downloaded:
            print(f"   ✅ {rk}")
        for rk, err in errors:
            print(f"   ❌ {rk}: {err}")

        print(f"Downloaded {len(downloaded)} file(s).")
        return

    object_key = app.provider.resolve_path(
        app.current_prefix, remote_path_arg, is_directory=False
    )
    if not object_key or object_key.endswith('/'):
        print("Error: Invalid file path for get.")
        return

    basename = os.path.basename(object_key)
    if local_dest_arg:
        if local_dest_arg.endswith(os.path.sep) or os.path.isdir(local_dest_arg):
            dest_path = os.path.join(local_dest_arg, basename)
        else:
            dest_path = local_dest_arg
    else:
        dest_path = os.path.join(os.getcwd(), basename)

    try:
        print(f"Downloading {object_key} to {dest_path}...")
        app.provider.download_file(object_key, dest_path)
        print("Download successful.")
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        print(f"Error downloading file: {error_code}")
    except Exception as e:
        print(f"Error during get: {e}")


def do_put(app, *args):
    """Upload a local file using the provider."""
    if len(args) != 2:
        print("Usage: put <local_path> <remote_path>")
        return
    local_path, remote_path_arg = args

    if not os.path.isfile(local_path):
        print(f"Error: Local file '{local_path}' not found or is not a file.")
        return

    try:
        is_directory = remote_path_arg.endswith('/')
        resolved_remote_path = app.provider.resolve_path(
            app.current_prefix, remote_path_arg, is_directory=is_directory
        )

        if is_directory:
            target_key = resolved_remote_path + os.path.basename(local_path)
        else:
            target_key = resolved_remote_path
            if not target_key or target_key.endswith('/'):
                print(f"Error: Invalid target remote file path: {remote_path_arg}")
                return

        print(f"Uploading {local_path} to {target_key}...")
        app.provider.upload_file(local_path, target_key)
        print("Upload successful.")
        app.invalidate_cache_for_key(target_key)

    except Exception as e:
        print(f"Error during put: {e}")


# ---------------------------------------------------------------------------
# mirror — recursive download preserving directory structure
# ---------------------------------------------------------------------------

_SIZE_UNITS = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}


def _parse_size(size_str):
    """Parse a human-readable size string like '100MB' into bytes."""
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)$', size_str.upper().strip())
    if not m:
        return None
    return int(float(m.group(1)) * _SIZE_UNITS[m.group(2)])


def _recursive_walk(app, prefix, max_depth, current_depth=0):
    """Recursively list all files under prefix up to max_depth.
    Uses parallel_walk for concurrent directory traversal.
    Returns a list of (full_key, file_info) tuples.
    """
    from ..parallel import parallel_walk, get_workers_from_app

    workers = get_workers_from_app(app)
    walk_depth = max_depth if max_depth is not None else 50
    all_files, _, _ = parallel_walk(app, prefix, max_depth=walk_depth, workers=workers)
    return all_files


def do_mirror(app, *args):
    """Recursively download a remote prefix preserving directory structure.

    Usage: mirror <remote_prefix/> [local_dir] [options]
    Options:
      --depth N        Max recursion depth (default: unlimited)
      --max-size SIZE  Skip files larger than SIZE (default: 100MB)
      --dry-run        Show what would be downloaded without downloading
      --include PAT    Only include files matching glob pattern
      --exclude PAT    Exclude files matching glob pattern
      --flat           Download all files into a single directory
    """
    arg_list = list(args)
    remote_prefix = ''
    local_dir = None
    depth = None
    max_size = 100 * 1024 * 1024  # 100MB default
    dry_run = False
    include_pat = None
    exclude_pat = None
    flat = False

    i = 0
    positional = []
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--depth' and i + 1 < len(arg_list):
            try:
                depth = int(arg_list[i + 1])
            except ValueError:
                print("Invalid depth: " + arg_list[i + 1])
                return
            i += 2
        elif arg == '--max-size' and i + 1 < len(arg_list):
            parsed = _parse_size(arg_list[i + 1])
            if parsed is None:
                print("Invalid size: " + arg_list[i + 1] + " (e.g. 100MB, 50KB)")
                return
            max_size = parsed
            i += 2
        elif arg == '--dry-run':
            dry_run = True
            i += 1
        elif arg == '--include' and i + 1 < len(arg_list):
            include_pat = arg_list[i + 1]
            i += 2
        elif arg == '--exclude' and i + 1 < len(arg_list):
            exclude_pat = arg_list[i + 1]
            i += 2
        elif arg == '--flat':
            flat = True
            i += 1
        elif arg == '--help':
            print("Usage: mirror <remote_prefix/> [local_dir] [--depth N] [--max-size SIZE]")
            print("             [--dry-run] [--include PAT] [--exclude PAT] [--flat]")
            return
        elif not arg.startswith('-'):
            positional.append(arg)
            i += 1
        else:
            print("Unknown option: " + arg)
            return

    if not positional:
        print("Usage: mirror <remote_prefix/> [local_dir] [options]")
        return

    remote_prefix = positional[0]
    if len(positional) > 1:
        local_dir = positional[1]

    # Resolve remote prefix
    if remote_prefix == '.':
        prefix = app.current_prefix
    else:
        prefix = app.provider.resolve_path(app.current_prefix, remote_prefix, is_directory=True)

    # Default local dir
    if local_dir is None:
        bucket_name = getattr(app.provider, 'bucket_name', 'bucket')
        local_dir = os.path.join('.', 'mirror-' + bucket_name)
        if prefix:
            local_dir = os.path.join(local_dir, prefix.rstrip('/'))

    from ..parallel import parallel_walk, parallel_download, get_workers_from_app

    workers = get_workers_from_app(app)
    depth_str = str(depth) if depth is not None else '∞'
    print("🔄 Mirroring %s (depth: %s, max-size: %s)" % (
        prefix or '/', depth_str, human_readable_size(max_size)))
    if dry_run:
        print("   (dry run — no files will be downloaded)")
    print("   → %s" % os.path.abspath(local_dir))
    print()

    # Collect all files using parallel walk
    walk_depth = depth if depth is not None else 50
    print("   Scanning...", file=sys.stderr)
    all_files, _, _ = parallel_walk(app, prefix, max_depth=walk_depth, workers=workers)

    # Apply filters
    to_download = []
    skipped_size = 0
    skipped_filter = 0

    for full_key, f in all_files:
        basename = f['name']
        file_size = f.get('size', 0)

        if include_pat and not fnmatch.fnmatch(basename, include_pat):
            skipped_filter += 1
            continue
        if exclude_pat and fnmatch.fnmatch(basename, exclude_pat):
            skipped_filter += 1
            continue
        if file_size > max_size:
            skipped_size += 1
            continue

        # For dry run, just display
        if dry_run:
            size_str = human_readable_size(file_size)
            print("   %-55s %9s" % (full_key, size_str))

        # Remap keys relative to prefix for local paths
        rel_path = full_key[len(prefix):] if full_key.startswith(prefix) else full_key
        to_download.append((full_key, file_size))

    if dry_run:
        total_bytes = sum(sz for _, sz in to_download)
        print()
        print("📦 Would download: %d files (%s)" % (len(to_download), human_readable_size(total_bytes)))
        if skipped_size:
            print("   Skipped (too large): %d" % skipped_size)
        if skipped_filter:
            print("   Skipped (filtered):  %d" % skipped_filter)
        print()
        return

    if not to_download:
        print("   No files to download.")
        print()
        return

    # Build file keys with paths relative to prefix for local directory structure
    # parallel_download uses full keys, so we remap the local_base_dir
    # to account for the prefix stripping
    def _progress(completed, total, current_file):
        sys.stdout.write("\r   Downloaded: %d / %d  %s" % (completed, total, current_file[-40:] if current_file else ''))
        sys.stdout.flush()

    # We need to strip the prefix from keys for local path structure
    # Use a temp approach: download with full keys, using a base dir that accounts for prefix
    # Better: download manually with parallel_download using remapped keys
    download_keys = []
    for full_key, file_size in to_download:
        rel_path = full_key[len(prefix):] if full_key.startswith(prefix) else full_key
        download_keys.append((full_key, file_size))

    downloaded_list, skipped_list, error_list = parallel_download(
        app, download_keys, local_dir, workers=workers, flat=flat,
        progress_callback=_progress,
    )

    # Clear progress line
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()

    total_bytes = sum(sz for _, sz in to_download if any(rk == _ for rk, _ in downloaded_list))
    # Recalculate from what was actually downloaded
    total_bytes = 0
    for rk, lp in downloaded_list:
        for fk, fs in to_download:
            if fk == rk:
                total_bytes += fs
                break

    # Summary
    print()
    print("📦 Downloaded: %d files (%s)" % (len(downloaded_list), human_readable_size(total_bytes)))
    if skipped_size:
        print("   Skipped (too large): %d" % skipped_size)
    if skipped_filter:
        print("   Skipped (filtered):  %d" % skipped_filter)
    if error_list:
        print("   Errors: %d" % len(error_list))
        for rk, err in error_list[:5]:
            print("     ❌ %s: %s" % (rk, err))
    print("   Saved to: %s" % os.path.abspath(local_dir))
    print()


# ---------------------------------------------------------------------------
# diff — compare two files (local or remote)
# ---------------------------------------------------------------------------

def _is_local_path(path):
    """Check if a path refers to a local file."""
    return path.startswith('./') or path.startswith('~/') or path.startswith('/')


def _resolve_file(app, path, temp_dir):
    """Resolve a file path to a local file. Downloads remote files to temp_dir.
    Returns (local_path, display_name).
    """
    if _is_local_path(path):
        expanded = os.path.expanduser(path)
        if not os.path.isfile(expanded):
            raise FileNotFoundError("Local file not found: %s" % expanded)
        return expanded, path
    else:
        key = app.provider.resolve_path(app.current_prefix, path, is_directory=False)
        local_path = os.path.join(temp_dir, os.path.basename(key) + '.remote')
        app.provider.download_file(key, local_path)
        return local_path, key


def _is_text_file(path):
    """Heuristic check if a file is text."""
    try:
        with open(path, 'rb') as f:
            chunk = f.read(8192)
        chunk.decode('utf-8')
        return True
    except (UnicodeDecodeError, IOError):
        return False


def _file_sha256(path):
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def do_diff(app, *args):
    """Compare two files (local or remote).

    Usage: diff <file_a> <file_b>
    Paths starting with ./ ~/ or / are local; others are remote.
    """
    if len(args) != 2:
        print("Usage: diff <file_a> <file_b>")
        print("  Paths starting with ./ ~/ or / are treated as local files.")
        print("  Other paths are treated as remote bucket objects.")
        return

    temp_dir = tempfile.mkdtemp(prefix='bb-diff-')
    try:
        try:
            path_a, name_a = _resolve_file(app, args[0], temp_dir)
        except Exception as e:
            print("❌ Error resolving %s: %s" % (args[0], e))
            return

        try:
            path_b, name_b = _resolve_file(app, args[1], temp_dir)
        except Exception as e:
            print("❌ Error resolving %s: %s" % (args[1], e))
            return

        is_text_a = _is_text_file(path_a)
        is_text_b = _is_text_file(path_b)

        if is_text_a and is_text_b:
            with open(path_a, 'r', errors='replace') as f:
                lines_a = f.readlines()
            with open(path_b, 'r', errors='replace') as f:
                lines_b = f.readlines()

            diff_lines = list(difflib.unified_diff(
                lines_a, lines_b,
                fromfile=name_a, tofile=name_b,
            ))

            if not diff_lines:
                print("✅ Files are identical.")
                return

            print()
            for line in diff_lines:
                line = line.rstrip('\n')
                if line.startswith('+++') or line.startswith('---'):
                    print("\033[1m%s\033[0m" % line)
                elif line.startswith('+'):
                    print("\033[32m%s\033[0m" % line)
                elif line.startswith('-'):
                    print("\033[31m%s\033[0m" % line)
                elif line.startswith('@@'):
                    print("\033[36m%s\033[0m" % line)
                else:
                    print(line)
            print()
        else:
            # Binary comparison
            size_a = os.path.getsize(path_a)
            size_b = os.path.getsize(path_b)
            hash_a = _file_sha256(path_a)
            hash_b = _file_sha256(path_b)

            print()
            print("Binary comparison:")
            print("  %-40s  %9s  sha256:%s" % (name_a, human_readable_size(size_a), hash_a[:16]))
            print("  %-40s  %9s  sha256:%s" % (name_b, human_readable_size(size_b), hash_b[:16]))
            print()
            if hash_a == hash_b:
                print("✅ Files are identical.")
            else:
                print("❌ Files differ.")
            print()

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
