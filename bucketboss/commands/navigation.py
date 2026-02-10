import os
import sys

from ..formatting import format_dir_entry, format_file_entry


def do_ls(app, *args):
    """List objects using the cloud provider."""
    path = ''
    detailed = False
    sort_key = 'name'
    arg_list = list(args)
    try:
        while arg_list and arg_list[0].startswith('-'):
            opt = arg_list.pop(0)
            if opt == '-l':
                detailed = True
            elif opt.startswith('--sort='):
                sort_key = opt.split('=')[1].lower()
                if sort_key not in ['name', 'date', 'size']:
                    raise ValueError("Invalid sort key (name|date|size)")
            elif opt == '--help':
                print("Usage: ls [-l] [--sort=name|date|size] [path]")
                return
            else:
                raise ValueError(f"Unknown option: {opt}")
    except (ValueError, IndexError) as e:
        print(f"Invalid option: {e}")
        return

    path = ' '.join(arg_list) if arg_list else ''
    # If a file (not ending with slash) is specified, check and show file info
    if path and not path.endswith('/'):
        file_key = app.provider.resolve_path(app.current_prefix, path, is_directory=False)
        if '/' in file_key:
            parent_prefix = file_key.rsplit('/', 1)[0] + '/'
        else:
            parent_prefix = ''
        _, files, _ = app.list_objects(parent_prefix)
        for f in files:
            if f['name'] == os.path.basename(file_key):
                print(format_file_entry(f, detailed))
                return

    prefix = app.provider.resolve_path(app.current_prefix, path, is_directory=True)

    try:
        next_token = None
        limit = 50

        while True:
            dirs, files, next_token = app.list_objects(
                prefix, sort_key, limit=limit, next_token=next_token
            )

            all_entries = [
                *((d, 'dir') for d in dirs),
                *((f, 'file') for f in files),
            ]

            if not all_entries and next_token is None:
                print("No objects found.")
                break

            lines = []
            for entry, entry_type in all_entries:
                if entry_type == 'dir':
                    lines.append(format_dir_entry(entry))
                else:
                    lines.append(format_file_entry(entry, detailed))

            print('\n'.join(lines))

            if next_token:
                print(f"--- More ({len(all_entries)} items displayed) --- Press 'q' to quit, any other key for next page...")
                choice = app._get_single_char_input("")
                if choice == 'q':
                    break
            else:
                break

    except Exception as e:
        print(f"Error during ls: {e}")


def do_cd(app, *args):
    """Change the current remote prefix after verifying existence."""
    if len(args) != 1:
        print("Usage: cd <path>")
        return

    path_arg = args[0]
    original_prefix = app.current_prefix

    try:
        potential_new_prefix = app.provider.resolve_path(original_prefix, path_arg, is_directory=True)

        if potential_new_prefix == original_prefix and path_arg not in ('/', '', '.'):
            print(f"Already in '{potential_new_prefix}'")
            pass
        elif potential_new_prefix == original_prefix and path_arg in ('/', '', '.'):
            app.current_prefix = potential_new_prefix
            return

        target_dir_name = path_arg.split('/')[-1] or path_arg.split('/')[-2]
        if not target_dir_name or target_dir_name == '.':
            target_dir_name = potential_new_prefix.rstrip('/').split('/')[-1]

        parent_to_check = original_prefix
        if target_dir_name == '..':
            app.current_prefix = potential_new_prefix
            print(f"Changed directory to: {app.current_prefix or '/'}")
            return
        elif potential_new_prefix == '/' or potential_new_prefix == '':
            parent_to_check = ''
            target_dir_name = path_arg.strip('/')
            if not target_dir_name:
                app.current_prefix = ''
                return
        else:
            parent_to_check = (
                potential_new_prefix.rsplit('/', 2)[0] + '/'
                if '/' in potential_new_prefix.rstrip('/')
                else ''
            )
            target_dir_name = potential_new_prefix.rstrip('/').split('/')[-1]

        print(
            f"[Checking parent: '{parent_to_check or '<root>'}' for '{target_dir_name}']",
            file=sys.stderr,
        )
        parent_dirs, _, _ = app.list_objects(parent_to_check)

        if target_dir_name in parent_dirs:
            app.current_prefix = potential_new_prefix
        else:
            print(f"Error: Directory not found: {path_arg}")

    except Exception as e:
        print(f"Error changing directory: {e}")


# ---------------------------------------------------------------------------
# tree — visual directory tree
# ---------------------------------------------------------------------------

def _tree_walk(app, prefix, max_depth, current_depth, line_prefix):
    """Recursively build tree lines with box-drawing characters."""
    lines = []
    dirs, files, _ = app.list_objects(prefix)

    entries = []
    for d in dirs:
        entries.append((d + '/', True))
    for f in files:
        entries.append((f['name'], False))

    for idx, (name, is_dir) in enumerate(entries):
        is_last = (idx == len(entries) - 1)
        connector = '└── ' if is_last else '├── '
        lines.append(line_prefix + connector + name)

        if is_dir and current_depth < max_depth:
            extension = '    ' if is_last else '│   '
            sub_prefix = prefix + name
            sub_lines = _tree_walk(
                app, sub_prefix, max_depth, current_depth + 1,
                line_prefix + extension,
            )
            lines.extend(sub_lines)

    return lines


def do_tree(app, *args):
    """Display a visual directory tree.

    Usage: tree [path] [--depth N]
    Options:
      --depth N   Max depth to display (default: 3)
    """
    arg_list = list(args)
    path = None
    depth = 3

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
            print("Usage: tree [path] [--depth N]")
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

    root_label = app.provider.get_prompt_prefix() + prefix
    print(root_label)

    tree_lines = _tree_walk(app, prefix, depth, 0, '')
    for line in tree_lines:
        print(line)
    print()
