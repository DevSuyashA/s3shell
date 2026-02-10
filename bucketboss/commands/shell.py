import os


COMMAND_HELP = {
    'ls': """ls [-l] [--sort=name|date|size] [path]
  List objects and directories.
  -l              Detailed view with size and date
  --sort=KEY      Sort by name (default), date, or size""",

    'cd': """cd <path>
  Change the current remote directory.
  cd ..           Go up one level
  cd /            Go to bucket root
  cd data/        Enter the data/ directory""",

    'tree': """tree [path] [--depth N]
  Display a visual directory tree with box-drawing characters.
  --depth N       Max depth to display (default: 3)""",

    'pwd': """pwd
  Print the full current remote path.""",

    'cat': """cat <file>
  Display the full contents of a remote file.""",

    'peek': """peek <file>
  Preview a file — shows hex dump for binary, text for text files.""",

    'head': """head <file> [lines]
  Show the first N lines of a remote file (default: 10).""",

    'open': """open <file>
  Download a file to a temp location and open it with the system viewer.""",

    'get': """get <remote_path> [local_path]
  Download a remote file. Supports glob wildcards (e.g. get *.pem).
  If local_path is omitted, saves to the current local directory.""",

    'put': """put <local_path> <remote_path>
  Upload a local file to a remote path.""",

    'mirror': """mirror <remote_prefix/> [local_dir] [options]
  Recursively download a remote prefix preserving directory structure.
  --depth N        Max recursion depth (default: unlimited)
  --max-size SIZE  Skip files larger than SIZE (default: 100MB)
  --dry-run        Show what would be downloaded without downloading
  --include PAT    Only include files matching glob pattern
  --exclude PAT    Exclude files matching glob pattern
  --flat           Download all files into a single directory""",

    'diff': """diff <file_a> <file_b>
  Compare two files. Paths starting with ./ ~/ or / are local files;
  other paths are treated as remote bucket objects.
  Text files: colorized unified diff. Binary: size and SHA-256 comparison.""",

    'enum': """enum [path] [--depth N] [--download] [--min-severity LEVEL] [--no-classify]
  Enumerate and classify files by security relevance.
  --depth N              Max crawl depth (default: 5)
  --download             Auto-download CRITICAL files
  --min-severity LEVEL   Filter: critical|high|medium|info (default: info)
  --no-classify          Raw listing without classification""",

    'enum_report': """enum_report [--format text|json|md]
  Output the full results from the last enum run.
  --format FMT    Output format: text (default), json, or md""",

    'scope': """scope [path]
  Full recursive scan showing total objects, size, file types,
  directory structure depth, and date ranges.""",

    'th': """th <file_or_dir> [--depth N] [--verified-only] [--json] [--keep] [--max-size SIZE]
  Run TruffleHog against remote files to find secrets.
  th status       Check if trufflehog is installed
  --depth N       Max recursion depth for directories (default: 3)
  --verified-only Only show verified secrets
  --json          Output raw JSON findings
  --keep          Keep downloaded temp files after scan
  --max-size N    Skip files larger than N bytes (default: 50MB)""",

    'tag': """tag <file> <tag_text>
  Tag a file with a finding/note for later review.""",

    'findings': """findings [--severity LEVEL]
  List all tagged findings from the current session.""",

    'export': """export [--format text|json|md] [--output FILE]
  Export all findings to a file or stdout.""",

    'find': """find <pattern> [--path prefix] [--depth N]
  Find objects by name pattern (glob matching on filename).
  --path PREFIX   Search under PREFIX (default: current directory)
  --depth N       Max recursion depth (default: 5)""",

    'du': """du [path] [--depth N]
  Disk usage summary — shows size of each subdirectory.
  --depth N       Depth to report (default: 1)""",

    'stats': """stats
  Display collected bucket statistics and cached content summary.""",

    'info': """info <file>
  Show full metadata for a file (size, date, content-type, full key).""",

    'crawlstatus': """crawlstatus
  Display the status of the background cache crawl.""",

    'help': """help [command]
  Show available commands or detailed help for a specific command.""",

    'clear': """clear
  Clear the terminal screen.""",

    'exit': """exit
  Save cache and exit BucketBoss.""",

    'quit': """quit
  Save cache and exit BucketBoss (alias for exit).""",
}


COMMAND_CATEGORIES = [
    ('Navigation', ['ls', 'cd', 'tree', 'pwd']),
    ('Reading', ['cat', 'peek', 'head', 'open']),
    ('Transfer', ['get', 'put', 'mirror', 'diff']),
    ('Search', ['find']),
    ('Recon', ['enum', 'enum_report', 'scope', 'th']),
    ('Findings', ['tag', 'findings', 'export']),
    ('Info', ['stats', 'du', 'info', 'crawlstatus']),
    ('Shell', ['help', 'clear', 'exit', 'quit']),
]


def do_exit(app, *args):
    """Exit the shell."""
    print("Saving cache...")
    app._save_cache()
    print("Exiting...")
    return False


def do_clear(app, *args):
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def do_help(app, *args):
    """Show available commands or detailed help for a specific command."""
    if args:
        cmd_name = args[0].lower()
        if cmd_name in COMMAND_HELP:
            print()
            print(COMMAND_HELP[cmd_name])
            print()
        elif cmd_name in app.commands:
            print("  No detailed help available for '%s'." % cmd_name)
        else:
            print("  Unknown command: %s" % cmd_name)
        return

    print("\nBucketBoss Commands:\n")
    for category, cmds in COMMAND_CATEGORIES:
        available = [c for c in cmds if c in app.commands]
        if available:
            print("  \033[1m%s\033[0m" % category)
            print("    " + '  '.join(available))
            print()
    print("Type 'help <command>' for detailed usage. Use TAB for completion.")