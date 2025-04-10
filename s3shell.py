#!/usr/bin/env python3

import argparse
import os
import shlex
import tempfile
import platform
import subprocess
import botocore
import boto3
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style

from botocore.exceptions import ClientError
from datetime import datetime
from itertools import islice
import sys # Added for stderr in cache message

# --- S3 Completer --- 
class S3Completer(Completer):
    # Define commands that expect S3 paths/dirs/files as arguments
    s3_path_commands = {'ls', 'cd', 'cat', 'open'}
    # 'put' needs special handling (local first, then S3)

    def __init__(self, s3_shell_app):
        self.app = s3_shell_app # Store reference to the main app

    def _get_s3_suggestions(self, prefix_to_list, include_files=False):
        """Helper to get S3 directory and file suggestions for a given prefix."""
        try:
            # Use cache if available
            if prefix_to_list in self.app.cache:
                dirs, files = self.app.cache[prefix_to_list]
            else:
                # Fetch suggestions if not cached
                dirs, files = self.app.list_objects(prefix_to_list)
                self.app.cache[prefix_to_list] = (dirs, files)
            
            # Format suggestions: directories end with /, files don't
            suggestions = [d + '/' for d in dirs]
            if include_files:
                # Use the name from the file info dictionary
                suggestions += [f['name'] for f in files] 
            return suggestions
        except Exception as e:
            # Log error or handle gracefully
            # print(f"Error getting S3 suggestions: {e}", file=sys.stderr)
            return []

    def _get_local_suggestions(self, text):
        """Complete local filesystem paths."""
        try:
            path = os.path.expanduser(text) # Expand ~
            dir_path = os.path.dirname(path)
            partial = os.path.basename(path)

            if not dir_path: # If path is just a filename in cwd
                 dir_path = '.'
            elif not os.path.isdir(dir_path):
                 return [] # Base directory doesn't exist

            completions = []
            for name in os.listdir(dir_path):
                if name.startswith(partial):
                    # Construct the full path for checking type
                    full_item_path = os.path.join(dir_path, name)
                    # Construct the completion text relative to input `text`
                    # If text had a path sep, maintain it
                    completion_text = os.path.join(os.path.dirname(text), name) 
                    
                    if os.path.isdir(full_item_path):
                        # Add trailing slash for directories
                        completions.append(completion_text + '/')
                    else:
                        completions.append(completion_text)
            return completions
        except Exception as e:
            # print(f"Error getting local suggestions: {e}", file=sys.stderr)
            return []

    def get_completions(self, document, complete_event):
        text_before_cursor = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)
        
        try:
            # Use shlex to split, handling quotes
            parts = shlex.split(text_before_cursor)
            num_parts = len(parts)
        except ValueError:
             # If shlex fails (e.g., unmatched quotes), fallback to simple split
             parts = text_before_cursor.split()
             num_parts = len(parts)

        # Determine if the cursor is right after a space, indicating start of a new word
        completing_new_word = text_before_cursor.endswith(' ')

        try:
            # --- Case 1: Completing the command name --- 
            if num_parts == 0 or (num_parts == 1 and not completing_new_word):
                for cmd in sorted(self.app.commands.keys()):
                    if cmd.startswith(word):
                        yield Completion(cmd, start_position=-len(word))
                return # Stop after yielding commands

            # --- Case 2: Completing arguments --- 
            command = parts[0].lower()

            # --- Subcase: 'put' command (special handling) ---
            if command == 'put':
                # Completing the first argument (local path) or space after 'put'
                if num_parts == 1 and completing_new_word:
                     suggestions = self._get_local_suggestions('') # Complete from cwd
                     for suggestion in suggestions:
                         yield Completion(suggestion, start_position=0)
                elif num_parts == 2 and not completing_new_word:
                     suggestions = self._get_local_suggestions(parts[1]) # Complete partial local path
                     # Yield completions relative to the start of the word being completed
                     start_pos = -len(document.get_word_before_cursor(WORD=True))
                     for suggestion in suggestions:
                         yield Completion(suggestion, start_position=start_pos)

                # Completing the second argument (S3 path) or space after local path
                elif num_parts == 2 and completing_new_word:
                     # Complete S3 path, starting from current prefix
                     path_to_complete = '' # Start completion from current dir
                     resolved_prefix = self.app.resolve_path(path_to_complete, is_directory=True)
                     suggestions = self._get_s3_suggestions(resolved_prefix, include_files=True)
                     for suggestion in suggestions:
                         yield Completion(suggestion, start_position=0)
                elif num_parts == 3 and not completing_new_word:
                     # Complete partial S3 path
                     path_to_complete = parts[2]
                     if '/' in path_to_complete:
                         dir_part, partial = path_to_complete.rsplit('/', 1)
                         dir_part += '/'
                     else:
                         dir_part = ''
                         partial = path_to_complete
                     
                     resolved_prefix = self.app.resolve_path(dir_part, is_directory=True)
                     suggestions = self._get_s3_suggestions(resolved_prefix, include_files=True)
                     start_pos = -len(document.get_word_before_cursor(WORD=True))
                     for s in suggestions:
                         if s.startswith(partial):
                             # Construct the full suggestion based on dir_part
                             full_suggestion = dir_part + s 
                             yield Completion(full_suggestion, start_position=start_pos)
                return # Stop after handling 'put'

            # --- Subcase: Commands needing S3 path completion ---
            if command in self.s3_path_commands:
                 # Should complete if cursor is after space following command, or if typing the first arg
                 if (num_parts == 1 and completing_new_word) or (num_parts == 2 and not completing_new_word):
                      path_to_complete = ''
                      start_pos = 0
                      if num_parts == 2:
                           path_to_complete = parts[1]
                           start_pos = -len(document.get_word_before_cursor(WORD=True))
                      
                      if '/' in path_to_complete:
                           dir_part, partial = path_to_complete.rsplit('/', 1)
                           dir_part += '/'
                      else:
                           dir_part = ''
                           partial = path_to_complete

                      resolved_prefix = self.app.resolve_path(dir_part, is_directory=True)
                      # For cd, only suggest directories; for others, suggest files too
                      include_files = (command != 'cd') 
                      suggestions = self._get_s3_suggestions(resolved_prefix, include_files=include_files)

                      for s in suggestions:
                           if s.startswith(partial):
                                full_suggestion = dir_part + s
                                yield Completion(full_suggestion, start_position=start_pos)
                 return # Stop after handling S3 path commands

        except Exception as e:
            # Log completion errors to stderr to avoid breaking the UI
            print(f"Completer Error: {e}\nText: '{text_before_cursor}'", file=sys.stderr)
            # You might want more detailed logging or specific error handling here
            pass # Prevent completer crashes from stopping the shell

# --- S3Shell Application Class --- (Replaces cmd.Cmd class)
class S3ShellApp:
    def __init__(self, bucket_name, s3_client):
        self.bucket_name = bucket_name
        self.s3_client = s3_client
        self.current_prefix = ''
        self.cache = {}  # {prefix: (directories, files)}
        self.history = FileHistory(os.path.join(os.path.expanduser("~"), ".s3shell_history"))
        self.session = PromptSession(
            history=self.history,
            completer=S3Completer(self), # Pass app instance to completer
            complete_style=CompleteStyle.COLUMN # Example style
            # We can add custom styles later
        )
        self.commands = {
            'exit': self.do_exit,
            'quit': self.do_exit, # Alias
            # Other commands will be added here
            'ls': self.do_ls,
            'cd': self.do_cd,
            'cat': self.do_cat,
            'open': self.do_open,
            'put': self.do_put,
            'clear': self.do_clear,
            'help': self.do_help,
        }

    def get_prompt(self):
        """Generate the prompt string based on the current prefix."""
        base_path = f"s3://{self.bucket_name}/"
        if self.current_prefix:
            full_path = f'{base_path}{self.current_prefix}'
        else:
            full_path = base_path
        return f'{full_path}> '

    def run(self):
        """Main loop to run the shell application."""
        print("S3 Shell (prompt_toolkit). Type 'help' or 'exit'.") # Simple intro
        while True:
            try:
                text = self.session.prompt(self.get_prompt())
                if not text.strip():
                    continue
                if not self.handle_command(text):
                    break
            except KeyboardInterrupt:
                continue # Handle Ctrl+C
            except EOFError:
                print("\nExiting...")
                break # Handle Ctrl+D

    def handle_command(self, text):
        """Parse and execute the entered command."""
        try:
            parts = shlex.split(text.strip())
            if not parts:
                return True # Continue loop
            
            command_name = parts[0].lower()
            args = parts[1:]

            if command_name in self.commands:
                # Call the method associated with the command
                # Pass the S3ShellApp instance (self) and the arguments
                should_continue = self.commands[command_name](*args)
                return should_continue if should_continue is not None else True
            else:
                print(f"Unknown command: {command_name}")
                return True # Continue loop
        except Exception as e:
            print(f"Error processing command: {e}")
            return True # Continue loop on error

    # --- Basic Command Implementations --- (More will be ported later)
    def do_exit(self, *args):
        """Exit the shell."""
        print("Exiting...")
        return False # Signal to stop the loop

    # --- Ported Command Implementations ---
    def do_ls(self, *args):
        """List objects with optional sorting and pagination."""
        # Parse arguments manually from *args (shlex.split already done)
        path = ''
        detailed = False
        sort_key = 'name'
        page_size = 200 # Keep pagination simple for now
        max_lines_without_pagination = 200
        
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
        prefix = self.resolve_path(path, is_directory=True)
        
        try:
            # Use cached data if available
            if prefix in self.cache:
                directories, files = self.cache[prefix]
            else:
                directories, files = self.list_objects(prefix, sort_key)
                self.cache[prefix] = (directories, files)
            
            all_entries = [
                *((d, 'dir') for d in directories),
                *((f, 'file') for f in files) # Store full file info dict
            ]

            if not all_entries:
                 print("No objects found.")
                 return
            
            # Simplified display for now (no pagination)
            # TODO: Re-implement pagination if needed
            for entry, entry_type in all_entries:
                if entry_type == 'dir':
                    print(self._format_dir_entry(entry))
                else:
                    # entry is the file_info dict here
                    print(self._format_file_entry(entry, detailed))

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error listing objects: {error_code}")
        except Exception as e:
             print(f"Error during ls: {e}")

    def do_cd(self, *args):
        """Change the current S3 prefix."""
        if len(args) != 1:
            print("Usage: cd <path>")
            return
        path = args[0]
        try:
             # Check if path exists (optional but good practice)
             # Note: This check might be slow or imperfect for S3 'directories'
             # Consider adding a specific check if needed
             new_prefix = self.resolve_path(path, is_directory=True)
             # Basic validation: list a single item to see if prefix seems valid
             # self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=new_prefix, MaxKeys=1)
             self.current_prefix = new_prefix
             # Prompt updates automatically via get_prompt()
        except Exception as e:
             print(f"Error changing directory: {e}")

    def do_cat(self, *args):
        """Display the contents of a text-based object."""
        if len(args) != 1:
            print("Usage: cat <object_key>")
            return
        object_key_arg = args[0]
        object_key = self.resolve_path(object_key_arg, is_directory=False)
        if not object_key or object_key.endswith('/'):
            print("Error: Invalid file path for cat.")
            return
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=object_key)
            content = response['Body'].read().decode('utf-8')
            print(content)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'NoSuchKey':
                print(f"Error: Object not found: {object_key}")
            else:
                print(f"Error accessing object: {error_code}")
        except UnicodeDecodeError:
            print("Error: Unable to decode the object as text (likely binary file). Try 'open'.")
        except Exception as e:
            print(f"Error during cat: {e}")

    def do_open(self, *args):
        """Download and open an object using the system's default application."""
        if len(args) != 1:
            print("Usage: open <object_key>")
            return
        object_key_arg = args[0]
        object_key = self.resolve_path(object_key_arg, is_directory=False)
        if not object_key or object_key.endswith('/'):
            print("Error: Invalid file path for open.")
            return
        
        temp_file = None
        try:
            # Create a temporary file with the original name if possible
            base_name = os.path.basename(object_key)
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{base_name}")
            temp_path = temp_file.name
            temp_file.close() # Close it so download_file can write to it

            print(f"Downloading {object_key} to temporary file...")
            self.s3_client.download_file(self.bucket_name, object_key, temp_path)
            print(f"Opening {temp_path}...")

            if platform.system() == 'Windows':
                os.startfile(temp_path)
            elif platform.system() == 'Darwin': # macOS
                subprocess.run(['open', temp_path], check=True)
            else: # Linux/other
                subprocess.run(['xdg-open', temp_path], check=True)

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error accessing object: {error_code}")
            if temp_file: os.unlink(temp_path) # Clean up temp file on error
        except FileNotFoundError:
             print(f"Error: Could not find system command ('open' or 'xdg-open') to open the file.")
             if temp_file: os.unlink(temp_path) # Clean up temp file
        except subprocess.CalledProcessError as e:
             print(f"Error opening file with system command: {e}")
             # Don't delete, user might want the temp file
        except Exception as e:
            print(f"Error during open: {e}")
            if temp_file: os.unlink(temp_path) # Clean up temp file on error
        # Note: We intentionally leave the temp file for successful opens
        # It's hard to know when the user is done with it.

    def do_put(self, *args):
        """Upload a file from the local filesystem to the specified S3 path."""
        if len(args) != 2:
            print("Usage: put <local_path> <s3_path>")
            return
        local_path, s3_path_arg = args
        
        if not os.path.isfile(local_path):
            print(f"Error: Local file '{local_path}' not found or is not a file.")
            return
            
        try:
            # Determine if target S3 path is intended as a directory
            is_directory = s3_path_arg.endswith('/')
            # Resolve the S3 path (could be relative or absolute)
            resolved_s3_path = self.resolve_path(s3_path_arg, is_directory=is_directory)
            
            if is_directory:
                # If target is a directory, append local filename
                target_key = resolved_s3_path + os.path.basename(local_path)
            else:
                # Otherwise, use the exact resolved path
                target_key = resolved_s3_path
                # Check if the target accidentally resolves to just the root or a prefix ending in /
                if not target_key or target_key.endswith('/'):
                     print(f"Error: Invalid target S3 file path: {s3_path_arg}")
                     return

            print(f"Uploading {local_path} to s3://{self.bucket_name}/{target_key}...")
            self.s3_client.upload_file(local_path, self.bucket_name, target_key)
            print(f"Upload successful.")
            # Invalidate cache for the directory containing the uploaded file
            self.invalidate_cache_for_key(target_key)

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error uploading file: {error_code}")
        except Exception as e:
            print(f"Error during put: {e}")
            
    def do_clear(self, *args):
        """Clear the terminal screen."""
        os.system('cls' if os.name == 'nt' else 'clear')

    def do_help(self, *args):
        """Show available commands."""
        print("\nAvailable commands:")
        # Simple help: just list command names
        for cmd in sorted(self.commands.keys()):
            print(f"  {cmd}")
        print("\nUse TAB for completion.")
        # TODO: Enhance help to show usage details later if needed

    # --- Helper methods ported from original class ---
    def list_objects(self, prefix, sort_key='name'):
        """List directories and files under a given prefix with sorting."""
        directories = []
        files = []
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            operation_parameters = {
                'Bucket': self.bucket_name,
                'Prefix': prefix,
                'Delimiter': '/',
            }
            
            for page in paginator.paginate(**operation_parameters):
                # Process directories (CommonPrefixes)
                for cp in page.get('CommonPrefixes', []):
                    dir_path = cp['Prefix']
                    # Get the directory name relative to the current prefix
                    dir_name = dir_path[len(prefix):].rstrip('/')
                    if dir_name: # Avoid listing the prefix itself if empty
                        directories.append(dir_name)
                
                # Process files (Contents)
                for obj in page.get('Contents', []):
                    file_key = obj['Key']
                    # Don't list the prefix itself if it's also a key
                    if file_key == prefix:
                        continue 
                    file_name = file_key[len(prefix):]
                    if file_name: # Ensure we don't add empty names
                        files.append({
                            'name': file_name,
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'],
                            # Store extension separately for icon lookup
                            'extension': os.path.splitext(file_name)[1].lower()
                        })
            
            # Sort directories alphabetically
            directories.sort()
            
            # Sort files based on the specified key
            if sort_key == 'name':
                files.sort(key=lambda x: x['name'])
            elif sort_key == 'date':
                files.sort(key=lambda x: x['last_modified'], reverse=True)
            elif sort_key == 'size':
                files.sort(key=lambda x: x['size'], reverse=True)
            
            return directories, files
            
        except ClientError as e:
             # Handle potential access denied specifically for listing
             error_code = e.response.get('Error', {}).get('Code', 'Unknown')
             print(f"Error listing objects at '{prefix}': {error_code}")
             return [], [] # Return empty lists on error
        except Exception as e:
            print(f"Error listing objects: {str(e)}")
            return [], []

    def _format_dir_entry(self, dir_name):
        """Format directory entries with icon and color."""
        # Basic formatting for now, can be enhanced
        icon = '📁 ' if platform.system() != 'Windows' else ''
        # Simple print, prompt_toolkit can handle colors differently if needed
        return f"{icon}{dir_name}/"

    def _format_file_entry(self, file_info, detailed=False):
        """Format file entries with optional details."""
        icon = self._get_file_icon(file_info['extension'])
        if not detailed:
            return f"{icon} {file_info['name']}"
        else:
            date_str = file_info['last_modified'].strftime('%Y-%m-%d %H:%M')
            size_str = self._human_readable_size(file_info['size'])
            # Align size for better readability
            return f"{icon} {date_str} {size_str:>9} {file_info['name']}"

    def _get_file_icon(self, extension):
        """Get appropriate icon for file type (can be customized)."""
        # Using a simple map, consider external libraries for more icons
        icon_map = {
            '.txt': '📄', '.md': '📄', '.pdf': '📄', '.log': '📄',
            '.jpg': '🖼', '.jpeg': '🖼', '.png': '🖼', '.gif': '🖼', '.svg': '🖼',
            '.py': '🐍', '.js': '🟨', '.html': '🌐', '.css': '🎨', '.json': '⚙️', '.yaml': '⚙️', '.yml': '⚙️',
            '.zip': '📦', '.gz': '📦', '.tar': '📦', '.rar': '📦', '.7z': '📦',
            '.mp3': '🎵', '.wav': '🎵', '.mp4': '🎥', '.mov': '🎥', '.avi': '🎥',
            '.csv': '📊', '.xls': '📊', '.xlsx': '📊', '.doc': '📝', '.docx': '📝',
            '': '📄'  # Default for files without extension or unknown types
        }
        return icon_map.get(extension, '📄') # Default icon

    def _human_readable_size(self, size_bytes):
        """Convert bytes to human-readable format (KB, MB, GB)."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        size = float(size_bytes)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0 or unit == 'TB':
                break
            size /= 1024.0
        return f"{size:.1f} {unit}"
        
    def resolve_path(self, input_path, is_directory=False):
        """Resolve an input path relative to the current prefix."""
        # Handle absolute paths (starting with /)
        if input_path.startswith('/'):
            # Treat absolute path from the root of the bucket
            path_parts = input_path.lstrip('/').split('/')
        else:
            # Handle relative paths
            current_parts = self.current_prefix.rstrip('/').split('/') if self.current_prefix else []
            input_parts = input_path.split('/')
            path_parts = current_parts + input_parts

        # Normalize the path (handle .., .)
        normalized_parts = []
        for part in path_parts:
            if part == '..':
                if normalized_parts:
                    normalized_parts.pop()
            # Ignore empty parts (e.g., from //) or current dir .
            elif part and part != '.': 
                normalized_parts.append(part)
        
        normalized_path = '/'.join(normalized_parts)
        
        # Append trailing slash if it's a directory and not the root
        if is_directory and normalized_path:
            normalized_path += '/'
        elif not is_directory and normalized_path.endswith('/') and normalized_path != '/':
             # If it's not supposed to be a directory, remove trailing slash unless it's the root
             normalized_path = normalized_path.rstrip('/')
             
        return normalized_path

    def invalidate_cache_for_key(self, key):
        """Remove cache entries for the parent directories of a modified key."""
        # Find the parent prefix (directory) of the key
        if '/' in key:
            parent_prefix = key.rsplit('/', 1)[0] + '/'
        else:
            parent_prefix = '' # Root directory
            
        # Invalidate the immediate parent
        if parent_prefix in self.cache:
            print(f"[Cache invalidated for: {parent_prefix}]", file=sys.stderr) # Print to stderr
            del self.cache[parent_prefix]
        # Invalidate the root cache if the key was in the root
        if '' in self.cache and '/' not in key:
             del self.cache['']
             
    # get_parent_prefixes might not be needed if we only invalidate immediate parent
    # def get_parent_prefixes(self, key):
    #     prefixes = set()
    #     parts = key.split('/')[:-1]
    #     current_path = []
    #     prefixes.add('') # Always include root
    #     for part in parts:
    #         if part:
    #             current_path.append(part)
    #             prefix = '/'.join(current_path) + '/'
    #             prefixes.add(prefix)
    #     return prefixes

# --- Argument Parsing and Client Creation (Mostly unchanged) --- 
def create_s3_client(args):
    if args.profile:
        session = boto3.Session(profile_name=args.profile)
        return session.client('s3')
    elif args.access_key and args.secret_key:
        return boto3.client(
            's3',
            aws_access_key_id=args.access_key,
            aws_secret_access_key=args.secret_key
        )
    else:
        # Default to unsigned if no credentials provided
        return boto3.client(
            's3',
            config=botocore.client.Config(signature_version=botocore.UNSIGNED)
        )

def parse_args():
    parser = argparse.ArgumentParser(description='S3 Interactive Shell (prompt_toolkit)') # Updated description
    parser.add_argument('--bucket', required=True, help='S3 bucket name')
    group = parser.add_argument_group('Authentication methods')
    group.add_argument('--profile', help='AWS CLI profile name')
    group.add_argument('--access-key', help='AWS access key')
    parser.add_argument('--secret-key', help='AWS secret key')
    args = parser.parse_args()
    if (args.access_key and not args.secret_key) or (args.secret_key and not args.access_key):
        parser.error('--access-key and --secret-key must be provided together')
    if sum(1 for x in [args.profile, args.access_key] if x) > 1:
        parser.error('Only one authentication method (--profile, --access-key) can be used.')
    return args

# --- Main Execution --- (Updated to use S3ShellApp)
def main():
    args = parse_args()
    try:
        s3_client = create_s3_client(args)
        # Verify bucket access
        s3_client.head_bucket(Bucket=args.bucket)
        print(f"Successfully connected to bucket: {args.bucket}")
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        if error_code == '404':
            print(f"Error: Bucket '{args.bucket}' not found or access denied.")
        elif error_code == '403':
             print(f"Error: Access denied to bucket '{args.bucket}'. Check credentials/permissions.")
        else:
             print(f"Error accessing bucket '{args.bucket}': {error_code}")
        return
    except Exception as e:
        print(f"Failed to create S3 client or connect to bucket: {e}")
        return

    # Create and run the application
    app = S3ShellApp(args.bucket, s3_client)
    app.run()

if __name__ == '__main__':
    main()
