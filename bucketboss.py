#!/usr/bin/env python3

import argparse
import os
import shlex
import tempfile
import platform
import subprocess
import botocore
import boto3
import sys
import threading # Import threading
import fnmatch  # For wildcard pattern matching
from abc import ABC, abstractmethod # For Abstract Base Class
from itertools import islice
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style
from prompt_toolkit.patch_stdout import patch_stdout

from botocore.exceptions import ClientError
from datetime import datetime

# Cache time-to-live in seconds (default 6 hours)
CACHE_TTL_SECONDS = 6 * 3600

# --- Cloud Provider Abstraction ---
class CloudProvider(ABC):
    """Abstract base class for cloud storage providers."""
    @abstractmethod
    def get_prompt_prefix(self) -> str:
        """Return the string prefix for the prompt (e.g., 's3://bucket/')."""
        pass

    @abstractmethod
    def head_bucket(self):
        """Check if the bucket exists and is accessible."""
        pass

    @abstractmethod
    def list_objects(self, prefix: str, sort_key: str = 'name') -> tuple[list[str], list[dict]]:
        """List directories (prefixes) and files (objects) under a given prefix."""
        pass

    @abstractmethod
    def resolve_path(self, current_prefix: str, input_path: str, is_directory: bool = False) -> str:
        """Resolve an input path relative to the current prefix for this provider."""
        pass

    @abstractmethod
    def get_object(self, key: str) -> bytes:
        """Get the content of an object as bytes."""
        pass

    @abstractmethod
    def download_file(self, key: str, local_path: str):
        """Download an object to a local file path."""
        pass

    @abstractmethod
    def upload_file(self, local_path: str, key: str):
        """Upload a local file to a specific object key."""
        pass
        
    @abstractmethod
    def get_bucket_stats(self) -> dict:
        """Get basic statistics about the bucket/container.""" # For Todo item 1
        pass

# --- S3 Implementation ---
class S3Provider(CloudProvider):
    def __init__(self, bucket_name: str, s3_client):
        self.bucket_name = bucket_name
        self.s3_client = s3_client

    def get_prompt_prefix(self) -> str:
        return f"s3://{self.bucket_name}/"

    def head_bucket(self):
        # This might raise ClientError if bucket not found or no permission
        self.s3_client.head_bucket(Bucket=self.bucket_name)

    def list_objects(self, prefix: str, sort_key: str = 'name') -> tuple[list[str], list[dict]]:
        directories = []
        files = []
        # Original list_objects logic using self.s3_client and self.bucket_name
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            operation_parameters = {
                'Bucket': self.bucket_name,
                'Prefix': prefix,
                'Delimiter': '/',
            }
            
            for page in paginator.paginate(**operation_parameters):
                for cp in page.get('CommonPrefixes', []):
                    dir_path = cp['Prefix']
                    dir_name = dir_path[len(prefix):].rstrip('/')
                    if dir_name:
                        directories.append(dir_name)
                
                for obj in page.get('Contents', []):
                    file_key = obj['Key']
                    if file_key == prefix:
                        continue 
                    file_name = file_key[len(prefix):]
                    if file_name:
                        files.append({
                            'name': file_name,
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'],
                            'extension': os.path.splitext(file_name)[1].lower()
                        })
            
            directories.sort()
            if sort_key == 'name':
                files.sort(key=lambda x: x['name'])
            elif sort_key == 'date':
                files.sort(key=lambda x: x['last_modified'], reverse=True)
            elif sort_key == 'size':
                files.sort(key=lambda x: x['size'], reverse=True)
            
            return directories, files
            
        except ClientError as e:
             error_code = e.response.get('Error', {}).get('Code', 'Unknown')
             print(f"Error listing S3 objects at '{prefix}': {error_code}", file=sys.stderr)
             raise # Re-raise for the caller to handle
        except Exception as e:
            print(f"Error listing S3 objects: {str(e)}", file=sys.stderr)
            raise # Re-raise

    def resolve_path(self, current_prefix: str, input_path: str, is_directory: bool = False) -> str:
        # Original resolve_path logic, using current_prefix
        if input_path.startswith('/'):
            path_parts = input_path.lstrip('/').split('/')
        else:
            current_parts = current_prefix.rstrip('/').split('/') if current_prefix else []
            input_parts = input_path.split('/')
            path_parts = current_parts + input_parts

        normalized_parts = []
        for part in path_parts:
            if part == '..':
                if normalized_parts:
                    normalized_parts.pop()
            elif part and part != '.': 
                normalized_parts.append(part)
        
        normalized_path = '/'.join(normalized_parts)
        
        if is_directory and normalized_path:
            normalized_path += '/'
        elif not is_directory and normalized_path.endswith('/') and normalized_path != '/':
             normalized_path = normalized_path.rstrip('/')
             
        return normalized_path

    def get_object(self, key: str) -> bytes:
        # May raise ClientError (e.g., NoSuchKey)
        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
        return response['Body'].read()

    def download_file(self, key: str, local_path: str):
        # May raise ClientError
        self.s3_client.download_file(self.bucket_name, key, local_path)

    def upload_file(self, local_path: str, key: str):
        # May raise ClientError
        self.s3_client.upload_file(local_path, self.bucket_name, key)
        
    def get_bucket_stats(self) -> dict:
        """Get S3 bucket location and creation date."""
        stats = {}
        try:
            # --- Get Location --- 
            try:
                location_response = self.s3_client.get_bucket_location(Bucket=self.bucket_name)
                # Handle potential None response defensively, though unlikely for this call
                if location_response:
                    stats['Location'] = location_response.get('LocationConstraint') or 'us-east-1'
                else:
                    stats['Location'] = "Error: Received no response for location."
            except ClientError as e_loc:
                stats['Location'] = f"Error: {e_loc.response.get('Error', {}).get('Code', 'Unknown')}"
            except Exception as e_loc_other: # Catch unexpected errors here
                 stats['Location'] = f"Unexpected error getting location: {str(e_loc_other)}"

            # --- Get Creation Date --- 
            try:
                list_response = self.s3_client.list_buckets()
                found_date = False
                # Check if response and 'Buckets' key exist
                if list_response and 'Buckets' in list_response:
                    for bucket in list_response.get('Buckets') or []: # Ensure iteration over list
                         # Check if bucket dict has Name and CreationDate
                         if bucket and bucket.get('Name') == self.bucket_name and bucket.get('CreationDate'):
                             stats['CreationDate'] = bucket['CreationDate'].isoformat()
                             found_date = True
                             break
                if not found_date:
                     # Only set this if we didn't successfully find it
                     if 'CreationDate' not in stats:
                          stats['CreationDate'] = "Not found (or requires list_buckets permission)"
            except ClientError as e_date:
                 # If we already have a date, don't overwrite with an error unless necessary
                 if 'CreationDate' not in stats:
                      stats['CreationDate'] = f"Error: {e_date.response.get('Error', {}).get('Code', 'Unknown')}"
            except Exception as e_date_other: # Catch unexpected errors here
                 if 'CreationDate' not in stats:
                      # Use a generic message instead of trying str(e_date_other)
                      stats['CreationDate'] = f"Unexpected error processing creation date data."

            # --- Size Placeholder --- 
            stats['Size'] = "N/A (Requires separate calculation)"
            
        except Exception as outer_e: 
             # Catch-all for any error during the overall process if needed, though inner catches are better
             print(f"General error during get_bucket_stats: {str(outer_e)}", file=sys.stderr)
             # Ensure basic stats structure exists even on outer error
             if 'Location' not in stats: stats['Location'] = "Error retrieving"
             if 'CreationDate' not in stats: stats['CreationDate'] = "Error retrieving"
             if 'Size' not in stats: stats['Size'] = "N/A"
             
        return stats

# --- BucketBoss Completer --- (Formerly S3Completer)
class BucketBossCompleter(Completer):
    # Define commands that expect remote paths/dirs/files as arguments
    # These will use provider.resolve_path and provider.list_objects
    remote_path_commands = {'ls', 'cd', 'cat', 'open', 'get'}
    # 'put' needs special handling (local first, then remote)

    def __init__(self, bucket_boss_app):
        self.app = bucket_boss_app # Store reference to the main app

    def _get_remote_suggestions(self, prefix_to_list, include_files=False):
        """Helper to get remote directory and file suggestions for a given prefix."""
        try:
            # Use centralized app-level list_objects for TTL caching
            dirs, files = self.app.list_objects(prefix_to_list)
            suggestions = [d + '/' for d in dirs]
            if include_files:
                suggestions += [f['name'] for f in files]
            return suggestions
        except Exception as e:
            # print(f"Error getting remote suggestions: {e}", file=sys.stderr)
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
            if not parts: return # Should not happen if num_parts > 0, but safety check
            command = parts[0].lower()

            # --- Subcase: 'put' command (local then remote) ---
            if command == 'put':
                # Completing the first argument (local path) 
                if (num_parts == 1 and completing_new_word) or (num_parts == 2 and not completing_new_word):
                     local_path_text = '' if completing_new_word else parts[1]
                     start_pos = 0 if completing_new_word else -len(document.get_word_before_cursor(WORD=True))
                     suggestions = self._get_local_suggestions(local_path_text)
                     for suggestion in suggestions:
                         yield Completion(suggestion, start_position=start_pos)

                # Completing the second argument (remote path)
                elif (num_parts == 2 and completing_new_word) or (num_parts == 3 and not completing_new_word):
                     remote_path_text = '' if completing_new_word else parts[2]
                     start_pos = 0 if completing_new_word else -len(document.get_word_before_cursor(WORD=True))
                     
                     if '/' in remote_path_text:
                         dir_part, partial = remote_path_text.rsplit('/', 1)
                         dir_part += '/'
                     else:
                         dir_part = ''
                         partial = remote_path_text
                     
                     # Use provider to resolve path relative to current app prefix
                     resolved_prefix = self.app.provider.resolve_path(self.app.current_prefix, dir_part, is_directory=True)
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

                      # Use provider to resolve path relative to current app prefix
                      resolved_prefix = self.app.provider.resolve_path(self.app.current_prefix, dir_part, is_directory=True)
                      include_files = (command != 'cd') 
                      suggestions = self._get_remote_suggestions(resolved_prefix, include_files=include_files)

                      for s in suggestions:
                           if s.startswith(partial):
                                full_suggestion = dir_part + s
                                yield Completion(full_suggestion, start_position=start_pos)
                 return 

        except Exception as e:
            print(f"Completer Error: {e}\nText: '{text_before_cursor}'", file=sys.stderr)
            pass

# --- BucketBoss Application Class --- (Formerly S3ShellApp)
class BucketBossApp:
    def __init__(self, provider: CloudProvider):
        self.provider = provider # Store the cloud provider instance
        # self.bucket_name = bucket_name # Now obtained via provider if needed
        # self.s3_client = s3_client # Now accessed via provider
        self.current_prefix = '' # Provider paths are relative to this
        self.cache = {}  # {prefix: (directories, files)} - cache remains here
        self.history = FileHistory(os.path.join(os.path.expanduser("~"), ".bucketboss_history")) # Renamed history
        self.session = PromptSession(
            history=self.history,
            completer=BucketBossCompleter(self), # Pass app instance
            complete_style=CompleteStyle.COLUMN 
        )
        self.commands = {
            'exit': self.do_exit,
            'quit': self.do_exit,
            'ls': self.do_ls,
            'cd': self.do_cd,
            'cat': self.do_cat,
            'open': self.do_open,
            'put': self.do_put,
            'clear': self.do_clear,
            'help': self.do_help,
            'stats': self.do_stats,
            'crawlstatus': self.do_crawl_status, # Add crawl status command
            'get': self.do_get,
        }
        # Initialize placeholder for background stats collection
        self.stats_result = {"status": "pending"}
        # Initialize placeholder for background cache crawl
        self.crawl_status = {"status": "pending", "depth": 0, "cached_prefixes": 0} 

    def get_prompt(self):
        """Generate the prompt string using the provider."""
        base_path = self.provider.get_prompt_prefix()
        if self.current_prefix:
            # Assuming provider prefix already includes bucket etc.
            full_path = f'{base_path}{self.current_prefix}'
        else:
            full_path = base_path
        return f'{full_path}> '

    def run(self):
        """Main loop to run the shell application."""
        print("BucketBoss Shell. Type 'help' or 'exit'.") # Rebranded intro
        while True:
            try:
                # Ensure background prints don't break the prompt
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

    # --- Command Implementations (Delegate to Provider) ---
    def do_exit(self, *args):
        """Exit the shell."""
        print("Exiting...")
        return False

    def do_ls(self, *args):
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
            # Resolve file key without directory slash
            file_key = self.provider.resolve_path(self.current_prefix, path, is_directory=False)
            # Determine its parent prefix
            if '/' in file_key:
                parent_prefix = file_key.rsplit('/', 1)[0] + '/'
            else:
                parent_prefix = ''
            # List parent to get file metadata
            _, files = self.list_objects(parent_prefix)
            for f in files:
                if f['name'] == os.path.basename(file_key):
                    # Found the file, display entry and return
                    print(self._format_file_entry(f, detailed))
                    return
            # If not found, it's not a file—error out
            print(f"Error: '{path}' is not a directory or file not found.")
            return

        # Use provider to resolve path
        prefix = self.provider.resolve_path(self.current_prefix, path, is_directory=True)
        
        try:
            # Use cached data if available and not expired
            entry = self.cache.get(prefix)
            if entry and time.time() - entry[2] < CACHE_TTL_SECONDS:
                # Cache hit
                print(f"[Cache hit: {prefix}]", file=sys.stderr)
                directories, files = entry[0], entry[1]
                # Re-sort cached files if needed (cache stores unsorted)
                if sort_key == 'date':
                    files.sort(key=lambda x: x['last_modified'], reverse=True)
                elif sort_key == 'size':
                    files.sort(key=lambda x: x['size'], reverse=True)
                else: # Default name sort
                    files.sort(key=lambda x: x['name'])
                # Directories are assumed to be stored sorted in cache
            else:
                print(f"[Fetch: {prefix}]", file=sys.stderr)
                # Fetch fresh data and update cache
                directories, files = self.provider.list_objects(prefix, sort_key)
                self.cache[prefix] = (directories, files, time.time())
            
            all_entries = [
                *((d, 'dir') for d in directories),
                *((f, 'file') for f in files)
            ]

            if not all_entries:
                 print("No objects found.")
                 return
            
            # --- Pagination Logic --- 
            try:
                 term_height = os.get_terminal_size().lines
                 # Leave room for prompt, input, and messages
                 page_limit = max(1, term_height - 3) 
            except OSError: # Handle environments without a TTY
                 term_height = float('inf')
                 page_limit = float('inf')
            
            line_count = 0
            for entry, entry_type in all_entries:
                if entry_type == 'dir':
                    print(self._format_dir_entry(entry))
                else:
                    print(self._format_file_entry(entry, detailed))
                line_count += 1
                
                # Check if page limit reached
                if line_count >= page_limit and line_count < len(all_entries):
                    try:
                        more_prompt = "--More-- (Enter: next page, q: quit)"
                        choice = input(more_prompt).strip().lower()
                        # Erase the prompt line after input
                        print("\033[F\033[K", end='') 
                        if choice == 'q':
                             print("[Listing aborted by user]")
                             break
                        line_count = 0 # Reset for next page
                    except (KeyboardInterrupt, EOFError):
                         print("\n[Listing aborted by user]")
                         break # Exit loop on Ctrl+C/Ctrl+D during prompt
            # --- End Pagination Logic ---

        except Exception as e:
             print(f"Error during ls: {e}") # Provider should handle specific errors

    def do_cd(self, *args):
        """Change the current remote prefix after verifying existence."""
        if len(args) != 1:
            print("Usage: cd <path>")
            return
            
        path_arg = args[0]
        original_prefix = self.current_prefix
        
        try:
            # 1. Resolve the potential new path string
            potential_new_prefix = self.provider.resolve_path(original_prefix, path_arg, is_directory=True)
            
            # Prevent cd-ing into the same directory silently
            if potential_new_prefix == original_prefix and path_arg not in ('/', '', '.'):
                 # Allow cd . or cd / even if they resolve to the same place
                 # But block `cd existing_dir` if already in parent
                 print(f"Already in '{potential_new_prefix}'") # Or just return silently
                 # return
                 pass # Allow explicit cd into current dir for now, maybe change later
            elif potential_new_prefix == original_prefix and path_arg in ('/', '', '.'):
                 # Explicitly cd to root or current - always allow, don't validate further
                 self.current_prefix = potential_new_prefix
                 return
                 
            # 2. Validate the target directory exists
            target_dir_name = path_arg.split('/')[-1] or path_arg.split('/')[-2] # Get last non-empty part
            if not target_dir_name or target_dir_name == '.': # Handle cases like 'cd .' or 'cd dir/.'
                 target_dir_name = potential_new_prefix.rstrip('/').split('/')[-1]
                 
            # If target is '..', resolve first then check parent of resolved
            parent_to_check = original_prefix
            if target_dir_name == '..':
                # Already resolved by provider.resolve_path, just check existence of potential_new_prefix
                # We can list potential_new_prefix itself and see if it works
                 try:
                      print(f"[Validating prefix: {potential_new_prefix}]", file=sys.stderr)
                      self.provider.list_objects(potential_new_prefix) # Check if listing works
                      # If list_objects succeeds (doesn't raise), assume it's valid
                      self.current_prefix = potential_new_prefix
                      print(f"Changed directory to: {self.current_prefix or '/'}")
                      return
                 except Exception as e_check:
                      print(f"Error: Cannot cd to '{potential_new_prefix}': {e_check}")
                      return
            elif potential_new_prefix == '/' or potential_new_prefix == '': # Target is root
                parent_to_check = '' # Check root contents
                target_dir_name = path_arg.strip('/') # Check if root was the target
                if not target_dir_name:
                     self.current_prefix = '' # Allow cd /
                     return
                 # If path_arg was like /nonexistent, target_dir_name will be set
            else:
                 # Determine the parent prefix of the *potential* new prefix
                 parent_to_check = potential_new_prefix.rsplit('/', 2)[0] + '/' if '/' in potential_new_prefix.rstrip('/') else ''
                 # Target name is the last part of the potential new prefix
                 target_dir_name = potential_new_prefix.rstrip('/').split('/')[-1]
                 
            # List the parent directory to find the target
            print(f"[Checking parent: '{parent_to_check or '<root>'}' for '{target_dir_name}']", file=sys.stderr)
            parent_dirs, _ = self.list_objects(parent_to_check)
            
            # 3. Check if the target directory name is in the list from the parent
            if target_dir_name in parent_dirs:
                self.current_prefix = potential_new_prefix
                # Optionally print confirmation
                # print(f"Changed directory to: {self.current_prefix or '/'}")
            else:
                print(f"Error: Directory not found: {path_arg}")

        except Exception as e:
             print(f"Error changing directory: {e}")

    def do_cat(self, *args):
        """Display the contents of a text-based object using the provider."""
        if len(args) != 1:
            print("Usage: cat <object_key>")
            return
        object_key_arg = args[0]
        # Use provider to resolve path
        object_key = self.provider.resolve_path(self.current_prefix, object_key_arg, is_directory=False)
        if not object_key or object_key.endswith('/'):
            print("Error: Invalid file path for cat.")
            return
        try:
            # Use provider to get object content
            content_bytes = self.provider.get_object(object_key)
            content = content_bytes.decode('utf-8')
            
            # --- Pagination Logic for Cat --- 
            lines = content.splitlines()
            try:
                 term_height = os.get_terminal_size().lines
                 page_limit = max(1, term_height - 2) # Leave room for prompt
            except OSError:
                 term_height = float('inf')
                 page_limit = float('inf')
                 
            line_count = 0
            for i, line in enumerate(lines):
                 print(line)
                 line_count += 1
                 
                 if line_count >= page_limit and i < len(lines) - 1:
                      try:
                           more_prompt = "--More-- (Enter: next page, q: quit)"
                           choice = input(more_prompt).strip().lower()
                           print("\033[F\033[K", end='') # Erase prompt
                           if choice == 'q':
                                print("[Output aborted by user]")
                                break
                           line_count = 0 # Reset for next page
                      except (KeyboardInterrupt, EOFError):
                           print("\n[Output aborted by user]")
                           break
            # --- End Pagination Logic --- 
            
        except ClientError as e:
            # Catch S3 specific error for example, but should be general
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
        """Download and open an object using the provider."""
        if len(args) != 1:
            print("Usage: open <object_key>")
            return
        object_key_arg = args[0]
        # Use provider to resolve path
        object_key = self.provider.resolve_path(self.current_prefix, object_key_arg, is_directory=False)
        if not object_key or object_key.endswith('/'):
            print("Error: Invalid file path for open.")
            return
        
        temp_file = None
        try:
            base_name = os.path.basename(object_key) or "downloaded_file"
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{base_name}")
            temp_path = temp_file.name
            temp_file.close() 

            print(f"Downloading {object_key} to temporary file...")
            # Use provider to download
            self.provider.download_file(object_key, temp_path)
            print(f"Opening {temp_path}...")

            if platform.system() == 'Windows':
                os.startfile(temp_path)
            elif platform.system() == 'Darwin': 
                subprocess.run(['open', temp_path], check=True)
            else: 
                subprocess.run(['xdg-open', temp_path], check=True)

        except ClientError as e: # Example S3 error
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error accessing object: {error_code}")
            if temp_file: os.unlink(temp_path) 
        except FileNotFoundError:
             print(f"Error: Could not find system command ('open' or 'xdg-open').")
             if temp_file: os.unlink(temp_path) 
        except subprocess.CalledProcessError as e:
             print(f"Error opening file with system command: {e}")
        except Exception as e:
            print(f"Error during open: {e}")
            if temp_file: os.unlink(temp_path) 

    def do_put(self, *args):
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
            # Use provider to resolve the remote path
            resolved_remote_path = self.provider.resolve_path(self.current_prefix, remote_path_arg, is_directory=is_directory)
            
            if is_directory:
                target_key = resolved_remote_path + os.path.basename(local_path)
            else:
                target_key = resolved_remote_path
                if not target_key or target_key.endswith('/'):
                     print(f"Error: Invalid target remote file path: {remote_path_arg}")
                     return

            print(f"Uploading {local_path} to {target_key}...")
            # Use provider to upload
            self.provider.upload_file(local_path, target_key)
            print(f"Upload successful.")
            # Invalidate cache for the directory containing the uploaded file
            self.invalidate_cache_for_key(target_key)

        except Exception as e:
            print(f"Error during put: {e}") # Provider should raise specific errors
            
    def do_clear(self, *args):
        """Clear the terminal screen."""
        os.system('cls' if os.name == 'nt' else 'clear')

    def do_help(self, *args):
        """Show available commands."""
        print("\nAvailable commands:")
        for cmd in sorted(self.commands.keys()):
            print(f"  {cmd}")
        print("\nUse TAB for completion.")
        
    def do_stats(self, *args):
        """Display collected bucket statistics."""
        status = self.stats_result.get("status", "unknown")
        
        if status == "pending" or status == "loading":
            print("Stats collection initiated in background, not yet complete...")
        elif status == "error":
            print(f"Error collecting stats: {self.stats_result.get('error_message', 'Unknown error')}")
        elif status == "complete":
            print("Bucket Stats (collected in background):")
            for key, value in self.stats_result.items():
                if key not in ["status", "error_message"]: # Don't print meta keys
                    print(f"  {key}: {value}")
        else:
            print(f"Stats status unknown: {status}")

    def do_crawl_status(self, *args):
        """Display the status of the background cache crawl."""
        status = self.crawl_status.get("status", "unknown")
        depth = self.crawl_status.get("depth", 0)
        cached = self.crawl_status.get("cached_prefixes", 0)
        
        if status == "pending":
            print("Background cache crawl has not started yet.")
        elif status == "loading":
            print(f"Background cache crawl in progress... (Current Depth: {depth}, Prefixes Cached: {cached})")
        elif status == "complete":
            print(f"Background cache crawl complete. (Max Depth: {depth}, Prefixes Cached: {cached})")
        elif status == "error":
            print(f"Background cache crawl finished with an error: {self.crawl_status.get('error_message', 'Unknown error')}")
        else:
             print(f"Crawl status unknown: {status}")

    # --- Formatting and Cache Helpers (Remain mostly internal to App) ---
    def list_objects(self, prefix, sort_key='name'):
       # This app-level method now primarily manages the cache
       # It calls the provider's list_objects if needed
       entry = self.cache.get(prefix)
       if entry and time.time() - entry[2] < CACHE_TTL_SECONDS:
            return entry[0], entry[1]
       else:
            try:
                 print(f"[Fetch: {prefix}]", file=sys.stderr)
                 dirs, files = self.provider.list_objects(prefix, sort_key)
                 self.cache[prefix] = (dirs, files, time.time())
                 return dirs, files
            except Exception as e:
                 # Error already printed by provider, just return empty
                 return [], []

    def _format_dir_entry(self, dir_name):
        icon = '📁 ' if platform.system() != 'Windows' else ''
        return f"{icon}{dir_name}/"

    def _format_file_entry(self, file_info, detailed=False):
        icon = self._get_file_icon(file_info['extension'])
        if not detailed:
            return f"{icon} {file_info['name']}"
        else:
            date_str = file_info['last_modified'].strftime('%Y-%m-%d %H:%M')
            size_str = self._human_readable_size(file_info['size'])
            return f"{icon} {date_str} {size_str:>9} {file_info['name']}"

    def _get_file_icon(self, extension):
        icon_map = {
            '.txt': '📄', '.md': '📄', '.pdf': '📄', '.log': '📄',
            '.jpg': '🖼', '.jpeg': '🖼', '.png': '🖼', '.gif': '🖼', '.svg': '🖼',
            '.py': '🐍', '.js': '🟨', '.html': '🌐', '.css': '🎨', '.json': '⚙️', '.yaml': '⚙️', '.yml': '⚙️',
            '.zip': '📦', '.gz': '📦', '.tar': '📦', '.rar': '📦', '.7z': '📦',
            '.mp3': '🎵', '.wav': '🎵', '.mp4': '🎥', '.mov': '🎥', '.avi': '🎥',
            '.csv': '📊', '.xls': '📊', '.xlsx': '📊', '.doc': '📝', '.docx': '📝',
            '': '📄'  
        }
        return icon_map.get(extension, '📄') 

    def _human_readable_size(self, size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        size = float(size_bytes)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0 or unit == 'TB':
                break
            size /= 1024.0
        return f"{size:.1f} {unit}"
        
    # Resolve path is now primarily handled by the provider
    # def resolve_path(self, input_path, is_directory=False): ...

    def invalidate_cache_for_key(self, key):
        """Invalidate cache for the parent directory of a key."""
        # Resolve the path to ensure consistency (e.g. handle relative paths)
        # Assume key is already resolved correctly by the command before calling this
        if '/' in key:
            parent_prefix = key.rsplit('/', 1)[0] + '/'
        else:
            parent_prefix = ''
            
        if parent_prefix in self.cache:
            print(f"[Cache invalidated for: {parent_prefix}]", file=sys.stderr)
            del self.cache[parent_prefix]
        # Also invalidate root if parent is root
        if parent_prefix == '' and '' in self.cache:
             print(f"[Cache invalidated for: <root>]", file=sys.stderr)
             del self.cache['']

    def do_get(self, *args):
        """Download a remote file to local directory where BucketBoss was started."""
        if len(args) < 1 or len(args) > 2:
            print("Usage: get <remote_path> [<local_path>]")
            return
        remote_path_arg = args[0]
        local_dest_arg = args[1] if len(args) == 2 else None

        # Wildcard pattern support: download multiple matching files
        if any(ch in remote_path_arg for ch in ['*', '?']):
            # Determine directory part and pattern
            if '/' in remote_path_arg:
                dir_part, pattern = remote_path_arg.rsplit('/', 1)
                dir_part += '/'
            else:
                dir_part = ''
                pattern = remote_path_arg
            prefix = self.provider.resolve_path(self.current_prefix, dir_part, is_directory=True)
            # List objects under prefix
            _, files = self.list_objects(prefix)
            names = [f['name'] for f in files]
            matches = fnmatch.filter(names, pattern)
            if not matches:
                print(f"No matches for pattern: {remote_path_arg}")
                return
            for name in matches:
                key = prefix + name
                basename = name
                # Determine destination path
                if local_dest_arg:
                    if local_dest_arg.endswith(os.path.sep) or os.path.isdir(local_dest_arg):
                        dest = os.path.join(local_dest_arg, basename)
                    else:
                        dest = local_dest_arg
                else:
                    dest = os.path.join(os.getcwd(), basename)
                try:
                    print(f"Downloading {key} to {dest}...")
                    self.provider.download_file(key, dest)
                    print("Download successful.")
                except ClientError as e:
                    error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                    print(f"Error downloading {key}: {error_code}")
                except Exception as e:
                    print(f"Error during get {key}: {e}")
            return

        # Resolve remote key
        object_key = self.provider.resolve_path(self.current_prefix, remote_path_arg, is_directory=False)
        if not object_key or object_key.endswith('/'):
            print("Error: Invalid file path for get.")
            return

        basename = os.path.basename(object_key)
        if local_dest_arg:
            # If local_dest is a directory or ends with '/', treat as directory
            if local_dest_arg.endswith(os.path.sep) or os.path.isdir(local_dest_arg):
                dest_path = os.path.join(local_dest_arg, basename)
            else:
                dest_path = local_dest_arg
        else:
            dest_path = os.path.join(os.getcwd(), basename)

        try:
            print(f"Downloading {object_key} to {dest_path}...")
            self.provider.download_file(object_key, dest_path)
            print("Download successful.")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error downloading file: {error_code}")
        except Exception as e:
            print(f"Error during get: {e}")

# --- Argument Parsing and Client Creation --- 
def create_s3_client(args):
    # (Logic remains the same)
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
        return boto3.client(
            's3',
            config=botocore.client.Config(signature_version=botocore.UNSIGNED)
        )

def parse_args():
    # Add provider argument later if needed
    parser = argparse.ArgumentParser(description='BucketBoss - Interactive Cloud Storage Shell') # Rebranded
    # --- S3 Specific Args --- (Consider moving to provider-specific parsing)
    parser.add_argument('--bucket', required=True, help='S3 bucket name')
    group = parser.add_argument_group('S3 Authentication methods')
    group.add_argument('--profile', help='AWS CLI profile name for S3')
    group.add_argument('--access-key', help='AWS access key for S3')
    parser.add_argument('--secret-key', help='AWS secret key for S3')
    # --- End S3 Specific --- 
    args = parser.parse_args()
    
    # S3 specific validation
    if (args.access_key and not args.secret_key) or (args.secret_key and not args.access_key):
        parser.error('S3 --access-key and --secret-key must be provided together')
    if sum(1 for x in [args.profile, args.access_key] if x) > 1:
        parser.error('Only one S3 authentication method (--profile, --access-key) can be used.')
    return args

# --- Background Stats Collection ---
def collect_stats_background(provider: CloudProvider, result_dict: dict):
    """Target function for background thread to collect stats."""
    result_dict["status"] = "loading"
    try:
        stats = provider.get_bucket_stats()
        result_dict.update(stats)
        result_dict["status"] = "complete"
    except Exception as e:
        result_dict["status"] = "error"
        # Store a user-friendly error message
        if isinstance(e, ClientError):
             result_dict["error_message"] = f"API Error: {e.response.get('Error', {}).get('Code', 'Unknown')}"
        else:
             result_dict["error_message"] = f"Unexpected error: {str(e)}"
    # Optional: print notification to stderr when done?
    # print("[Stats collection thread finished]", file=sys.stderr)

# --- Background Cache Crawl --- 
def crawl_prefix_recursive(provider: CloudProvider, cache: dict, status_dict: dict, prefix: str, current_depth: int, max_depth: int):
    """Recursively list and cache directories up to max_depth."""
    if current_depth > max_depth:
        return

    # Update status for user feedback
    status_dict["depth"] = max(status_dict.get("depth", 0), current_depth)

    # Cache-aware fetch: avoid re-fetching if TTL not expired
    entry = cache.get(prefix)
    if entry and time.time() - entry[2] < CACHE_TTL_SECONDS:
        dirs = entry[0]
        print(f"[Crawl: Using cached prefix '{prefix or '<root>'}' at depth {current_depth}]", file=sys.stderr)
    else:
        try:
            print(f"[Crawl: Fetching prefix '{prefix or '<root>'}' at depth {current_depth}]", file=sys.stderr)
            dirs, files = provider.list_objects(prefix)
            cache[prefix] = (dirs, files, time.time())
            status_dict["cached_prefixes"] = status_dict.get("cached_prefixes", 0) + 1
        except Exception as e:
            print(f"[Crawl: Error listing prefix '{prefix or '<root>'}': {e}]", file=sys.stderr)
            # Don't recurse further down this path if listing failed
            return

    # Recurse into subdirectories
    for subdir in dirs:
        if subdir: # Ensure subdir is not empty
            next_prefix = prefix + subdir + '/'
            crawl_prefix_recursive(provider, cache, status_dict, next_prefix, current_depth + 1, max_depth)

def background_cache_crawl(provider: CloudProvider, cache: dict, status_dict: dict, max_depth: int):
    """Target function for background thread to crawl and cache."""
    status_dict["status"] = "loading"
    status_dict["depth"] = 0
    status_dict["cached_prefixes"] = 0
    try:
        print(f"[Background crawl started: Max Depth {max_depth}]", file=sys.stderr)
        # Start crawling from the root prefix ('') at depth 1 (for its children)
        crawl_prefix_recursive(provider, cache, status_dict, '', 1, max_depth)
        status_dict["status"] = "complete"
        print(f"[Background crawl finished. Max Depth: {status_dict['depth']}, Prefixes Cached: {status_dict['cached_prefixes']}]", file=sys.stderr)
    except Exception as e:
        status_dict["status"] = "error"
        status_dict["error_message"] = f"Unexpected error during crawl: {str(e)}"
        print(f"[Background crawl failed: {e}]", file=sys.stderr)

# --- Main Execution --- 
def main():
    args = parse_args()
    provider = None
    
    # --- Provider Instantiation (Currently only S3) ---
    try:
        s3_client = create_s3_client(args)
        provider = S3Provider(args.bucket, s3_client)
        # Verify connection using provider method
        provider.head_bucket() 
        print(f"Successfully connected to S3 bucket: {args.bucket}")
        
        # --- Start Stats Collection in Background --- 
        # Create the app instance first
        app = BucketBossApp(provider)
        
        # Start the background thread, passing the provider and the app's result dict
        print("Initiating background stats collection...")
        stats_thread = threading.Thread(
            target=collect_stats_background, 
            args=(provider, app.stats_result), 
            daemon=True # Ensure thread doesn't block exit
        )
        stats_thread.start()
        # --- Stats collection started, do not wait here --- 
        
        # --- Start Background Cache Crawl (Depth 2) ---
        crawl_depth = 2 # Configurable depth
        if crawl_depth > 0:
             print(f"Initiating background cache crawl (max depth {crawl_depth})...")
             crawl_thread = threading.Thread(
                  target=background_cache_crawl,
                  args=(provider, app.cache, app.crawl_status, crawl_depth),
                  daemon=True
             )
             crawl_thread.start()
        # --- Cache crawl started --- 
             
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        if error_code in ['404', 'NoSuchBucket']:
            print(f"Error: S3 Bucket '{args.bucket}' not found or access denied.")
        elif error_code in ['403', 'AccessDenied']:
             print(f"Error: Access denied to S3 bucket '{args.bucket}'. Check credentials/permissions.")
        else:
             print(f"Error accessing S3 bucket '{args.bucket}': {error_code}")
        return
    except Exception as e:
        print(f"Failed to create S3 client or connect: {e}")
        return
    # --- End Provider Instantiation ---

    if not provider:
         print("Error: Could not initialize cloud provider.")
         return
         
    # Run the application (stats thread runs concurrently)
    app.run()

if __name__ == '__main__':
    main()
