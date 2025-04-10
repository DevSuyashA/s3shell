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

# --- Placeholder Completer --- (Will be replaced later)
class S3Completer(Completer):
    def get_completions(self, document, complete_event):
        # Basic command completion for now
        commands = ['ls', 'cd', 'put', 'cat', 'open', 'exit', 'clear', 'help']
        word = document.get_word_before_cursor()
        if document.text_before_cursor.strip() == "" or ' ' not in document.text_before_cursor.strip():
             for cmd in commands:
                 if cmd.startswith(word):
                     yield Completion(cmd, start_position=-len(word))

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
            completer=S3Completer(), # Use placeholder completer for now
            complete_style=CompleteStyle.COLUMN # Example style
            # We can add custom styles later
        )
        self.commands = {
            'exit': self.do_exit,
            'quit': self.do_exit, # Alias
            # Other commands will be added here
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

    # --- Helper methods to be ported later ---
    # _get_suggestions
    # _complete_path (will be part of S3Completer)
    # _complete_local_path (will be part of S3Completer)
    # do_ls
    # list_objects
    # _format_dir_entry
    # _format_file_entry
    # _get_file_icon
    # _human_readable_size
    # do_cd
    # do_cat
    # do_open
    # do_clear
    # do_put
    # resolve_path
    # invalidate_cache_for_key
    # get_parent_prefixes

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
