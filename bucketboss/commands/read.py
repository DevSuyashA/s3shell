import os
import platform
import subprocess
import tempfile

from botocore.exceptions import ClientError

from ..formatting import human_readable_size


def do_cat(app, *args):
    """Display the contents of a text-based object using the provider."""
    if len(args) != 1:
        print("Usage: cat <object_key>")
        return
    object_key_arg = args[0]
    object_key = app.provider.resolve_path(app.current_prefix, object_key_arg, is_directory=False)
    if not object_key or object_key.endswith('/'):
        print("Error: Invalid file path for cat.")
        return

    # SAFETY CHECK: Check size before downloading
    try:
        meta = app.provider.get_object_metadata(object_key)
        size = meta.get('size', 0)
        human_size = human_readable_size(size)

        if size > 1024 * 1024:  # > 1MB
            print(f"Warning: File is large ({human_size}).")
            choice = app._get_single_char_input("Display anyway? [y/N/p(eek)]: ")
            print()
            if choice == 'p':
                do_peek(app, object_key_arg)
                return
            if choice != 'y':
                return
    except Exception:
        pass

    try:
        content_bytes = app.provider.get_object(object_key)
        content = content_bytes.decode('utf-8')

        import pydoc
        pydoc.pager(content)

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        if error_code == 'NoSuchKey':
            print(f"Error: Object not found: {object_key}")
        else:
            print(f"Error accessing object: {error_code}")
    except UnicodeDecodeError:
        print("Error: Unable to decode the object as text (likely binary file). Try 'open' or 'peek'.")
    except Exception as e:
        print(f"Error during cat: {e}")


def do_peek(app, *args):
    """Peek at the first few bytes of a file (default 2KB). Usage: peek <file> [bytes]"""
    if not args:
        print("Usage: peek <file> [bytes]")
        return

    path = args[0]
    size = 2048
    if len(args) > 1:
        try:
            size = int(args[1])
            if size > 10 * 1024 * 1024:
                print("Error: Size limit is 10MB for peek")
                return
            if size <= 0:
                print("Error: Size must be positive")
                return
        except ValueError:
            print("Error: bytes must be an integer")
            return

    try:
        key = app.provider.resolve_path(app.current_prefix, path, is_directory=False)
        content = app.provider.read_object_range(key, size)

        try:
            text = content.decode('utf-8')
            print(f"--- First {size} bytes of {key} ---")
            print(text)
            print("\n--- End of Peek ---")
        except UnicodeDecodeError:
            print(f"--- First {size} bytes of {key} (Hex Dump) ---")
            import binascii
            print(binascii.hexlify(content).decode('ascii'))
            print("\n--- End of Peek ---")

    except ClientError as e:
        print(f"Error peeking object: {e}")
    except Exception as e:
        print(f"Error: {e}")


def do_open(app, *args):
    """Download and open an object using the provider."""
    if len(args) != 1:
        print("Usage: open <object_key>")
        return
    object_key_arg = args[0]
    object_key = app.provider.resolve_path(
        app.current_prefix, object_key_arg, is_directory=False
    )
    if not object_key or object_key.endswith('/'):
        print("Error: Invalid file path for open.")
        return

    temp_file = None
    temp_path = None
    try:
        base_name = os.path.basename(object_key) or "downloaded_file"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{base_name}")
        temp_path = temp_file.name
        temp_file.close()

        print(f"Downloading {object_key} to temporary file...")
        app.provider.download_file(object_key, temp_path)
        print(f"Opening {temp_path}...")

        if platform.system() == 'Windows':
            os.startfile(temp_path)
        elif platform.system() == 'Darwin':
            subprocess.run(['open', temp_path], check=True)
        else:
            subprocess.run(['xdg-open', temp_path], check=True)

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        print(f"Error accessing object: {error_code}")
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    except FileNotFoundError:
        print("Error: Could not find system command ('open' or 'xdg-open').")
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    except subprocess.CalledProcessError as e:
        print(f"Error opening file with system command: {e}")
    except Exception as e:
        print(f"Error during open: {e}")
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
