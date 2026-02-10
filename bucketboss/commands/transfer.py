import fnmatch
import os

from botocore.exceptions import ClientError


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
        for name in matches:
            key = prefix + name
            basename = name
            if local_dest_arg:
                if local_dest_arg.endswith(os.path.sep) or os.path.isdir(local_dest_arg):
                    dest = os.path.join(local_dest_arg, basename)
                else:
                    dest = local_dest_arg
            else:
                dest = os.path.join(os.getcwd(), basename)
            try:
                print(f"Downloading {key} to {dest}...")
                app.provider.download_file(key, dest)
                print("Download successful.")
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                print(f"Error downloading {key}: {error_code}")
            except Exception as e:
                print(f"Error during get {key}: {e}")
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
