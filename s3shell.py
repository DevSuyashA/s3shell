#!/usr/bin/env python3

import argparse
import cmd
import os
import shlex
import tempfile
import platform
import subprocess
import botocore
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
from itertools import islice

class S3Shell(cmd.Cmd):
    def __init__(self, bucket_name, s3_client):
        super().__init__()
        self.bucket_name = bucket_name
        self.s3_client = s3_client
        self.current_prefix = ''
        self.cache = {}  # {prefix: (directories, files)}
        self.update_prompt()
        
    def cmdloop(self, intro=None):
        """Wrap cmdloop to handle keyboard interrupts"""
        while True:
            try:
                super().cmdloop(intro="")
                break
            except KeyboardInterrupt:
                print("\nUse 'exit' to quit the shell or press Ctrl+D")
                self.do_clear('')
        
    def _get_suggestions(self, path, include_files=False):
        """Get directory and file suggestions for tab completion"""
        try:
            prefix = self.resolve_path(path, is_directory=True)
            if prefix not in self.cache:
                # Fetch suggestions if not cached
                self.cache[prefix] = self.list_objects(prefix)
            
            dirs, files = self.cache[prefix]
            suggestions = [d + '/' for d in dirs]
            if include_files:
                suggestions += [f['name'] for f in files]
            return suggestions
        except Exception:
            return []

    def _complete_path(self, text, line, include_files=False):
        """Generic path completion handler"""
        try:
            # Parse command line to get the current path argument
            args = shlex.split(line[:len(line) - len(text)].strip())
            args = args[1:]  # Remove command name

            if line.endswith(' '):
                current_path = text
            else:
                current_path = args[-1] if args else text

            # Split into directory components and partial name
            if '/' in current_path:
                dir_part, partial = current_path.rsplit('/', 1)
                dir_part += '/'
            else:
                dir_part = ''
                partial = current_path

            # Resolve the directory part relative to current prefix
            resolved_dir = self.resolve_path(dir_part, is_directory=True)
            
            # Get suggestions for this directory
            suggestions = self._get_suggestions(resolved_dir, include_files)
            
            # Filter matches for the partial name
            matches = [s for s in suggestions if s.startswith(partial)]
            
            # Format completions with proper paths
            completions = []
            for match in matches:
                if dir_part:
                    full_path = f"{dir_part}{match}"
                else:
                    full_path = match
                completions.append(full_path[len(current_path):])

            return completions
        
        except KeyboardInterrupt:
            return []
        except Exception as e:
            return []
        
    # Tab completion methods
    def complete_cd(self, text, line, begidx, endidx):
        """Tab completion for cd command"""
        return self._complete_path(text, line, include_files=False)

    def complete_ls(self, text, line, begidx, endidx):
        """Tab completion for ls command"""
        return self._complete_path(text, line, include_files=True)

    def complete_cat(self, text, line, begidx, endidx):
        """Tab completion for cat command"""
        return self._complete_path(text, line, include_files=True)

    def complete_open(self, text, line, begidx, endidx):
        """Tab completion for open command"""
        return self._complete_path(text, line, include_files=True)

    def complete_put(self, text, line, begidx, endidx):
        """Tab completion for put command"""
        try:
            args = shlex.split(line[:len(line) - len(text)])
            if len(args) >= 2 and not line.endswith(' '):
                # Complete remote path
                return self._complete_path(text, line, include_files=True)
            else:
                # Complete local files
                return [f for f in os.listdir('.') 
                       if f.startswith(text)]
        except Exception:
            return []

    # Enhanced help system
    def do_help(self, arg):
        """Show detailed help information"""
        help_text = """
Available commands:

  cd <path>        Change current directory
    Usage: cd [directory_path]
    Example: cd documents/2023/

  ls [options] [path]  List directory contents
    Options:
      -l           Detailed listing with metadata
      --sort=KEY   Sort by (name|date|size)
    Example: ls -l --sort=size documents/

  cat <file>       Display text file contents
    Example: cat readme.txt

  open <file>      Open file with default application
    Example: open image.jpg

  put <local> <remote>  Upload file to S3
    Example: put report.pdf backups/

  clear            Clear the screen
  exit             Exit the shell
  help [command]   Show this help or command-specific help

Use TAB completion for:
- Path suggestions while typing
- Local files in put command
- Command options
"""
        if arg:
            cmd_func = getattr(self, 'do_' + arg, None)
            if cmd_func and cmd_func.__doc__:
                print(f"\n{arg}: {cmd_func.__doc__.strip()}")
            else:
                print(f"\nNo help available for {arg}")
        else:
            print(help_text)

        
    def update_prompt(self):
        """Update the command prompt to display the current directory."""
        base_path = f"s3://{self.bucket_name}/"
        if self.current_prefix:
            full_path = f'{base_path}{self.current_prefix}'
        else:
            full_path = base_path
        self.prompt = f'{full_path}> '
        
    def do_ls(self, arg):
        """List objects with optional sorting and pagination
        Usage: ls [-l] [--sort=name|date|size] [path]
        """
        args = shlex.split(arg)
        path = ''
        detailed = False
        sort_key = 'name'
        page_size = 200
        max_lines_without_pagination = 200
        
        # Parse arguments
        try:
            while args and args[0].startswith('-'):
                opt = args.pop(0)
                if opt == '-l':
                    detailed = True
                elif opt.startswith('--sort='):
                    sort_key = opt.split('=')[1].lower()
                    if sort_key not in ['name', 'date', 'size']:
                        raise ValueError("Invalid sort key")
                elif opt == '--help':
                    print(self.do_ls.__doc__)
                    return
        except (ValueError, IndexError) as e:
            print(f"Invalid option: {e}")
            return

        path = ' '.join(args) if args else ''
        prefix = self.resolve_path(path, is_directory=True)
        
        try:
            if prefix in self.cache:
                directories, files = self.cache[prefix]
            else:
                directories, files = self.list_objects(prefix, sort_key)
                self.cache[prefix] = (directories, files)
            
            # Create all_entries AFTER fetching data
            all_entries = [
                *((d, 'dir') for d in directories),
                *((f['name'], 'file') for f in files)
            ]
            
            # Pagination control logic
            if len(all_entries) > max_lines_without_pagination:
                page = 0
                while True:
                    start_idx = page * page_size
                    current_page = list(islice(all_entries, start_idx, start_idx + page_size))
                    
                    if not current_page:
                        if page == 0:
                            print("No objects found")
                        break
                    
                    # Display current page
                    for entry, entry_type in current_page:
                        if entry_type == 'dir':
                            print(self._format_dir_entry(entry))
                        else:
                            file_info = next(f for f in files if f['name'] == entry)
                            print(self._format_file_entry(file_info, detailed))
                    try:
                        print(f"\n--- Page {page+1} - Press Enter to continue (q to quit) ---")
                        choice = input().strip().lower()
                    except KeyboardInterrupt:
                        print("\nPagination cancelled")
                        break
                                            
                    if choice == 'q':
                        break
                    page += 1
            else:
                # Display all results without pagination
                for entry, entry_type in all_entries:
                    if entry_type == 'dir':
                        print(self._format_dir_entry(entry))
                    else:
                        file_info = next(f for f in files if f['name'] == entry)
                        print(self._format_file_entry(file_info, detailed))
        except KeyboardInterrupt:
            print("\nListing cancelled")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error listing objects: {error_code}")

    def list_objects(self, prefix, sort_key='name'):
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            operation_parameters = {
                'Bucket': self.bucket_name,
                'Prefix': prefix,
                'Delimiter': '/',
            }
            
            common_prefixes = []
            objects = []
            for page in paginator.paginate(**operation_parameters):
                common_prefixes.extend(page.get('CommonPrefixes', []))
                objects.extend(page.get('Contents', []))
                
            # Process directories
            directories = []
            for cp in common_prefixes:
                dir_path = cp['Prefix']
                dir_name = dir_path[len(prefix):].rstrip('/')
                if dir_name:
                    directories.append(dir_name)
            
            # Process files with metadata
            files = []
            for obj in objects:
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
            
            # Sorting
            directories.sort()
            if sort_key == 'name':
                files.sort(key=lambda x: x['name'])
            elif sort_key == 'date':
                files.sort(key=lambda x: x['last_modified'], reverse=True)
            elif sort_key == 'size':
                files.sort(key=lambda x: x['size'], reverse=True)
            
            return directories, files
            
        except Exception as e:
            print(f"Error listing objects: {str(e)}")
            return [], []

    def _format_dir_entry(self, dir_name):
        """Format directory entries with icon and color"""
        icon = '📁 ' if platform.system() != 'Windows' else ''
        color_start = '\033[94m' if platform.system() != 'Windows' else ''
        color_end = '\033[0m' if platform.system() != 'Windows' else ''
        return f"{color_start}{icon}{dir_name}/{color_end}"

    def _format_file_entry(self, file_info, detailed=False):
        """Format file entries with optional details"""
        icon = self._get_file_icon(file_info['extension'])
        
        if not detailed:
            return f"{icon} {file_info['name']}"
            
        # Detailed format
        date_str = file_info['last_modified'].strftime('%Y-%m-%d %H:%M')
        size_str = self._human_readable_size(file_info['size'])
        return f"{icon} {date_str} {size_str:>8} {file_info['name']}"

    def _get_file_icon(self, extension):
        """Get appropriate icon for file type"""
        icon_map = {
            '.txt': '📄', '.md': '📄', '.pdf': '📄',
            '.jpg': '🖼 ', '.jpeg': '🖼 ', '.png': '🖼 ', '.gif': '🖼 ',
            '.py': '🐍', '.js': '🟨', '.html': '🌐', '.css': '🎨',
            '.zip': '📦', '.gz': '📦', '.tar': '📦',
            '.mp3': '🎵', '.mp4': '🎥', '.mov': '🎥',
            '.csv': '📊', '.xls': '📊', '.xlsx': '📊',
            '': '📄'  # Default for files without extension
        }
        return icon_map.get(extension, '📄')

    def _human_readable_size(self, size_bytes):
        """Convert bytes to human-readable format"""
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        size = float(size_bytes)
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                break
            size /= 1024.0
        return f"{size:.1f} {unit}"

        
    def do_cd(self, arg):
        """Change the current directory."""
        args = shlex.split(arg)
        if len(args) != 1:
            print("Usage: cd <path>")
            return
        path = args[0]
        new_prefix = self.resolve_path(path, is_directory=True)
        self.current_prefix = new_prefix
        self.update_prompt()
    
    def do_cat(self, arg):
        """Display the contents of a text-based object."""
        args = shlex.split(arg)
        if len(args) != 1:
            print("Usage: cat <object_key>")
            return
        object_key_arg = args[0]
        object_key = self.resolve_path(object_key_arg, is_directory=False)
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=object_key)
            content = response['Body'].read().decode('utf-8')
            print(content)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error accessing object: {error_code}")
        except UnicodeDecodeError:
            print("Error: Unable to decode the object as text.")
        except KeyboardInterrupt:
            print("\nPrinting Cancelled")
    
    def do_open(self, arg):
        """Open non-text files using the system's default application."""
        args = shlex.split(arg)
        if len(args) != 1:
            print("Usage: open <object_key>")
            return
        object_key_arg = args[0]
        object_key = self.resolve_path(object_key_arg, is_directory=False)
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_path = temp_file.name
                self.s3_client.download_file(self.bucket_name, object_key, temp_path)
            if platform.system() == 'Windows':
                os.startfile(temp_path)
            elif platform.system() == 'Darwin':
                subprocess.run(['open', temp_path])
            else:
                subprocess.run(['xdg-open', temp_path])
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error accessing object: {error_code}")
        except Exception as e:
            print(f"Error opening file: {e}")
        except KeyboardInterrupt:
            print("\nOpening Cancelled")
        
    
    def do_clear(self, arg):
        """Clear the terminal screen."""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def do_exit(self, arg):
        """Exit the shell."""
        print("Exiting...")
        return True
    
    def do_help(self, arg):
        """Display a list of available commands and their usage."""
        print("Available commands:")
        print("cd <path> - Change current directory")
        print("ls [path] - List objects")
        print("cat <object_key> - Display contents of a text object")
        print("open <object_key> - Open a file with the default application")
        print("clear - Clear the screen")
        print("put <local_path> <s3_path> - Upload a file")
        print("exit - Exit the shell")
        print("help - Show this help")
    
    def do_put(self, arg):
        """Upload a file from the local filesystem to the specified S3 path."""
        args = shlex.split(arg)
        if len(args) != 2:
            print("Usage: put <local_path> <s3_path>")
            return
        local_path, s3_path_arg = args
        if not os.path.isfile(local_path):
            print(f"Error: Local file '{local_path}' not found.")
            return
        is_directory = s3_path_arg.endswith('/')
        resolved_s3_path = self.resolve_path(s3_path_arg, is_directory=is_directory)
        if is_directory:
            target_key = resolved_s3_path + os.path.basename(local_path)
        else:
            target_key = resolved_s3_path
        try:
            self.s3_client.upload_file(local_path, self.bucket_name, target_key)
            print(f"Uploaded to s3://{self.bucket_name}/{target_key}")
            self.invalidate_cache_for_key(target_key)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error uploading file: {error_code}")
        except KeyboardInterrupt:
            print("\nUpload Cancelled")
    
    def resolve_path(self, input_path, is_directory=False):
        if input_path.startswith('/'):
            path_parts = input_path.lstrip('/').split('/')
        else:
            current_parts = self.current_prefix.rstrip('/').split('/') if self.current_prefix else []
            input_parts = input_path.split('/')
            path_parts = current_parts + input_parts
        normalized_parts = []
        for part in path_parts:
            if part == '..':
                if normalized_parts:
                    normalized_parts.pop()
            elif part in ('.', ''):
                continue
            else:
                normalized_parts.append(part)
        normalized_path = '/'.join(normalized_parts)
        if is_directory and normalized_path:
            normalized_path += '/'
        return normalized_path
    
    def invalidate_cache_for_key(self, key):
        parent_prefixes = self.get_parent_prefixes(key)
        for prefix in parent_prefixes:
            if prefix in self.cache:
                del self.cache[prefix]
    
    def get_parent_prefixes(self, key):
        prefixes = set()
        parts = key.split('/')[:-1]
        current_path = []
        for part in parts:
            current_path.append(part)
            prefix = '/'.join(current_path) + '/'
            prefixes.add(prefix)
        return prefixes

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
        return boto3.client(
            's3',
            config=botocore.client.Config(signature_version=botocore.UNSIGNED)
        )

def parse_args():
    parser = argparse.ArgumentParser(description='S3 Interactive Shell')
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

def main():
    args = parse_args()
    s3_client = create_s3_client(args)
    try:
        s3_client.head_bucket(Bucket=args.bucket)
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        print(f"Error accessing bucket: {error_code}")
        return
    shell = S3Shell(args.bucket, s3_client)
    shell.cmdloop()

if __name__ == '__main__':
    main()
