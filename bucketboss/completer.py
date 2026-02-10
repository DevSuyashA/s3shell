import os
import shlex

from prompt_toolkit.completion import Completer, Completion


class BucketBossCompleter(Completer):
    remote_path_commands = {
        'ls', 'cd', 'cat', 'open', 'get', 'peek', 'head', 'info',
        'tag', 'th', 'diff', 'find', 'mirror', 'enum', 'du', 'tree',
    }

    def __init__(self, bucket_boss_app):
        self.app = bucket_boss_app

    def _get_remote_suggestions(self, prefix_to_list, include_files=False):
        """Helper to get remote directory and file suggestions for a given prefix."""
        try:
            dirs, files, _ = self.app.list_objects(prefix_to_list)
            suggestions = [d + '/' for d in dirs]
            if include_files:
                suggestions += [f['name'] for f in files]
            return suggestions
        except Exception:
            return []

    def _get_local_suggestions(self, text):
        """Complete local filesystem paths."""
        try:
            path = os.path.expanduser(text)
            dir_path = os.path.dirname(path)
            partial = os.path.basename(path)

            if not dir_path:
                dir_path = '.'
            elif not os.path.isdir(dir_path):
                return []

            completions = []
            for name in os.listdir(dir_path):
                if name.startswith(partial):
                    full_item_path = os.path.join(dir_path, name)
                    completion_text = os.path.join(os.path.dirname(text), name)

                    if os.path.isdir(full_item_path):
                        completions.append(completion_text + '/')
                    else:
                        completions.append(completion_text)
            return completions
        except Exception:
            return []

    def get_completions(self, document, complete_event):
        text_before_cursor = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)

        try:
            parts = shlex.split(text_before_cursor)
            num_parts = len(parts)
        except ValueError:
            parts = text_before_cursor.split()
            num_parts = len(parts)

        completing_new_word = text_before_cursor.endswith(' ')

        try:
            # --- Case 1: Completing the command name ---
            if num_parts == 0 or (num_parts == 1 and not completing_new_word):
                for cmd in sorted(self.app.commands.keys()):
                    if cmd.startswith(word):
                        yield Completion(cmd, start_position=-len(word))
                return

            # --- Case 2: Completing arguments ---
            if not parts:
                return
            command = parts[0].lower()

            # --- Subcase: 'put' command (local then remote) ---
            if command == 'put':
                if (num_parts == 1 and completing_new_word) or (num_parts == 2 and not completing_new_word):
                    local_path_text = '' if completing_new_word else parts[1]
                    start_pos = 0 if completing_new_word else -len(document.get_word_before_cursor(WORD=True))
                    suggestions = self._get_local_suggestions(local_path_text)
                    for suggestion in suggestions:
                        yield Completion(suggestion, start_position=start_pos)

                elif (num_parts == 2 and completing_new_word) or (num_parts == 3 and not completing_new_word):
                    remote_path_text = '' if completing_new_word else parts[2]
                    start_pos = 0 if completing_new_word else -len(document.get_word_before_cursor(WORD=True))

                    if '/' in remote_path_text:
                        dir_part, partial = remote_path_text.rsplit('/', 1)
                        dir_part += '/'
                    else:
                        dir_part = ''
                        partial = remote_path_text

                    resolved_prefix = self.app.provider.resolve_path(
                        self.app.current_prefix, dir_part, is_directory=True
                    )
                    suggestions = self._get_remote_suggestions(resolved_prefix, include_files=True)

                    for s in suggestions:
                        if s.startswith(partial):
                            full_suggestion = dir_part + s
                            yield Completion(full_suggestion, start_position=start_pos)
                return

            # --- Subcase: Commands needing remote path completion ---
            if command in self.remote_path_commands:
                if (num_parts == 1 and completing_new_word) or (num_parts == 2 and not completing_new_word):
                    path_to_complete = '' if completing_new_word else parts[1]
                    start_pos = 0 if completing_new_word else -len(document.get_word_before_cursor(WORD=True))

                    if '/' in path_to_complete:
                        dir_part, partial = path_to_complete.rsplit('/', 1)
                        dir_part += '/'
                    else:
                        dir_part = ''
                        partial = path_to_complete

                    resolved_prefix = self.app.provider.resolve_path(
                        self.app.current_prefix, dir_part, is_directory=True
                    )
                    include_files = (command != 'cd')
                    suggestions = self._get_remote_suggestions(resolved_prefix, include_files=include_files)

                    for s in suggestions:
                        if s.startswith(partial):
                            full_suggestion = dir_part + s
                            yield Completion(full_suggestion, start_position=start_pos)
                return

        except Exception:
            pass
