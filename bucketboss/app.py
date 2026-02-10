import json
import os
import shlex
import sys
import time
import tty
import termios
from datetime import datetime
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.patch_stdout import patch_stdout

from .completer import BucketBossCompleter
from .providers.base import CloudProvider
from .commands.navigation import do_ls, do_cd
from .commands.read import do_cat, do_peek, do_open
from .commands.transfer import do_get, do_put
from .commands.info import do_stats, do_crawl_status, do_audit
from .commands.shell import do_exit, do_clear, do_help

# Cache time-to-live in seconds (default 6 hours)
CACHE_TTL_SECONDS = 6 * 3600


class BucketBossApp:
    def __init__(self, provider: CloudProvider):
        self.provider = provider
        self.current_prefix = ''
        self.cache = {}  # {prefix: (directories, files, timestamp)}
        self._load_cache()
        self.history = FileHistory(
            os.path.join(os.path.expanduser("~"), ".bucketboss_history")
        )
        self.session = PromptSession(
            history=self.history,
            completer=BucketBossCompleter(self),
            complete_style=CompleteStyle.COLUMN,
        )
        # Commands map to functions that take (app, *args)
        self.commands = {
            'exit': lambda *args: do_exit(self, *args),
            'quit': lambda *args: do_exit(self, *args),
            'ls': lambda *args: do_ls(self, *args),
            'cd': lambda *args: do_cd(self, *args),
            'cat': lambda *args: do_cat(self, *args),
            'open': lambda *args: do_open(self, *args),
            'put': lambda *args: do_put(self, *args),
            'clear': lambda *args: do_clear(self, *args),
            'help': lambda *args: do_help(self, *args),
            'stats': lambda *args: do_stats(self, *args),
            'crawlstatus': lambda *args: do_crawl_status(self, *args),
            'get': lambda *args: do_get(self, *args),
            'peek': lambda *args: do_peek(self, *args),
            'audit': lambda *args: do_audit(self, *args),
        }
        self.stats_result = {"status": "pending"}
        self.crawl_status = {"status": "pending", "depth": 0, "cached_prefixes": 0}

    def get_prompt(self):
        """Generate the prompt string using the provider."""
        base_path = self.provider.get_prompt_prefix()
        if self.current_prefix:
            full_path = f'{base_path}{self.current_prefix}'
        else:
            full_path = base_path
        return f'{full_path}> '

    def run(self):
        """Main loop to run the shell application."""
        print("BucketBoss Shell. Type 'help' or 'exit'.")
        while True:
            try:
                with patch_stdout():
                    text = self.session.prompt(self.get_prompt())
                if not text.strip():
                    continue
                if not self.handle_command(text):
                    break
            except KeyboardInterrupt:
                continue
            except EOFError:
                print("\nExiting...")
                break

    def handle_command(self, text):
        """Parse and execute the entered command."""
        try:
            parts = shlex.split(text.strip())
            if not parts:
                return True

            command_name = parts[0].lower()
            args = parts[1:]

            if command_name in self.commands:
                should_continue = self.commands[command_name](*args)
                return should_continue if should_continue is not None else True
            else:
                print(f"Unknown command: {command_name}")
                return True
        except Exception as e:
            print(f"Error processing command: {e}")
            return True

    def _get_single_char_input(self, prompt_message: str) -> str:
        """Gets a single character input from the terminal without requiring Enter."""
        sys.stdout.write(prompt_message)
        sys.stdout.flush()
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            char = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return char.lower()

    def _get_cache_file_path(self):
        """Constructs the path for the cache file."""
        cache_dir = os.path.join(os.path.expanduser("~"), ".bucketboss_cache")
        os.makedirs(cache_dir, exist_ok=True)
        bucket_identifier = getattr(self.provider, 'bucket_name', 'default_bucket')
        return os.path.join(cache_dir, f"{bucket_identifier}.cache.json")

    def _load_cache(self):
        """Loads the cache from a file."""
        cache_file = self._get_cache_file_path()
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    loaded_data = json.load(f)

                self.cache = {}
                for prefix, entry in loaded_data.items():
                    dirs, files_serializable, timestamp = entry
                    files = []
                    for file_info_s in files_serializable:
                        file_info = file_info_s.copy()
                        if 'last_modified' in file_info and isinstance(file_info['last_modified'], str):
                            try:
                                file_info['last_modified'] = datetime.fromisoformat(file_info['last_modified'])
                            except ValueError:
                                print(
                                    f"Warning: Could not parse date '{file_info['last_modified']}' "
                                    f"for {prefix}{file_info.get('name', '')}. Using current time.",
                                    file=sys.stderr,
                                )
                                file_info['last_modified'] = datetime.now()
                        files.append(file_info)
                    self.cache[prefix] = (dirs, files, timestamp)
                print(f"Loaded cache from {cache_file}", file=sys.stderr)
        except (FileNotFoundError, json.JSONDecodeError, TypeError) as e:
            print(
                f"Could not load cache from {cache_file}: {e}. Starting with an empty cache.",
                file=sys.stderr,
            )
            self.cache = {}
        except Exception as e:
            print(
                f"Unexpected error loading cache: {e}. Starting with an empty cache.",
                file=sys.stderr,
            )
            self.cache = {}

    def _save_cache(self):
        """Saves the current cache to a file."""
        cache_file = self._get_cache_file_path()
        try:
            serializable_cache = {}
            for prefix, entry in self.cache.items():
                dirs, files, timestamp = entry
                files_serializable = []
                for file_info in files:
                    file_info_s = file_info.copy()
                    if 'last_modified' in file_info_s and isinstance(file_info_s['last_modified'], datetime):
                        file_info_s['last_modified'] = file_info_s['last_modified'].isoformat()
                    files_serializable.append(file_info_s)
                serializable_cache[prefix] = (dirs, files_serializable, timestamp)

            with open(cache_file, 'w') as f:
                json.dump(serializable_cache, f, indent=2)
            print(f"Saved cache to {cache_file}", file=sys.stderr)
        except Exception as e:
            print(f"Error saving cache to {cache_file}: {e}", file=sys.stderr)

    def list_objects(self, prefix, sort_key='name', limit: Optional[int] = None, next_token: Optional[str] = None):
        """App-level list_objects with caching layer."""
        if limit is None and next_token is None:
            entry = self.cache.get(prefix)
            if entry and time.time() - entry[2] < CACHE_TTL_SECONDS:
                return entry[0], entry[1], None

        try:
            if not next_token:
                print(f"[Fetch: {prefix}]", file=sys.stderr)

            dirs, files, token = self.provider.list_objects(
                prefix, sort_key, limit=limit, next_token=next_token
            )

            if limit is None:
                self.cache[prefix] = (dirs, files, time.time())

            return dirs, files, token
        except Exception:
            return [], [], None

    def invalidate_cache_for_key(self, key):
        """Invalidate cache for the parent directory of a key."""
        if '/' in key:
            parent_prefix = key.rsplit('/', 1)[0] + '/'
        else:
            parent_prefix = ''

        if parent_prefix in self.cache:
            print(f"[Cache invalidated for: {parent_prefix}]", file=sys.stderr)
            del self.cache[parent_prefix]
        if parent_prefix == '' and '' in self.cache:
            print("[Cache invalidated for: <root>]", file=sys.stderr)
            del self.cache['']
