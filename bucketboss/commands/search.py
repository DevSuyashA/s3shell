import fnmatch
import sys
from datetime import datetime

from ..formatting import human_readable_size


def _recursive_walk(app, prefix, max_depth, current_depth=0):
    """Recursively list all files under prefix up to max_depth.
    Yields (full_key, file_info) tuples.
    """
    dirs, files, _ = app.list_objects(prefix)

    for f in files:
        full_key = prefix + f['name']
        yield full_key, f

    if current_depth >= max_depth:
        return

    for d in dirs:
        sub_prefix = prefix + d + '/'
        yield from _recursive_walk(app, sub_prefix, max_depth, current_depth + 1)


def _format_date(f):
    """Extract a YYYY-MM-DD date string from a file_info dict."""
    lm = f.get('last_modified')
    if not lm:
        return ''
    if isinstance(lm, datetime):
        return lm.strftime('%Y-%m-%d')
    return str(lm)[:10]


def do_find(app, *args):
    """Find objects by name pattern.

    Usage: find <pattern> [--path prefix] [--depth N]
    Options:
      --path PREFIX   Search under PREFIX (default: current directory)
      --depth N       Max recursion depth (default: 5)
    """
    arg_list = list(args)
    pattern = None
    search_path = None
    depth = 5

    i = 0
    while i < len(arg_list):
        arg = arg_list[i]
        if arg == '--path' and i + 1 < len(arg_list):
            search_path = arg_list[i + 1]
            i += 2
        elif arg == '--depth' and i + 1 < len(arg_list):
            try:
                depth = int(arg_list[i + 1])
            except ValueError:
                print('Invalid depth: ' + arg_list[i + 1])
                return
            i += 2
        elif arg == '--help':
            print('Usage: find <pattern> [--path prefix] [--depth N]')
            return
        elif not arg.startswith('-') and pattern is None:
            pattern = arg
            i += 1
        else:
            print('Unknown option: ' + arg)
            return

    if pattern is None:
        print('Usage: find <pattern> [--path prefix] [--depth N]')
        return

    if search_path:
        prefix = app.provider.resolve_path(app.current_prefix, search_path, is_directory=True)
    else:
        prefix = app.current_prefix

    search_display = prefix if prefix else '/'
    print('\N{LEFT-POINTING MAGNIFYING GLASS} Searching for %r under %s (depth: %d)...' % (pattern, search_display, depth))
    print()

    matches = 0
    scanned = 0

    for full_key, f in _recursive_walk(app, prefix, depth):
        scanned += 1
        basename = f['name']
        if fnmatch.fnmatch(basename, pattern):
            size_str = human_readable_size(f.get('size', 0))
            date_str = _format_date(f)
            print('   %-55s %9s   %s' % (full_key, size_str, date_str))
            matches += 1

        if scanned % 200 == 0:
            sys.stdout.write('\r   Scanned: %d objects...' % scanned)
            sys.stdout.flush()

    if scanned >= 200:
        sys.stdout.write('\r' + ' ' * 60 + '\r')
        sys.stdout.flush()

    print()
    print('Found %d match(es) out of %d objects scanned.' % (matches, scanned))
    print()