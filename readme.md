# bucketboss

Interactive Cloud Storage Shell for S3

## Installation

Install with uv and git:

```bash
uv add git+https://github.com/csek-comanage/bucketboss.git
```

For development:

```bash
uv add --editable git+https://github.com/csek-comanage/bucketboss.git
```

## Usage

Run the interactive shell:

```bash
bb
```

### Command Line Options

```bash
bb [options]

Options:
  --bucket BUCKET       S3 bucket name (optional; omit to list all buckets)
  --profile PROFILE      AWS CLI profile name for S3
  --access-key ACCESS_KEY  AWS access key for S3
  --secret-key SECRET_KEY  AWS secret key for S3
  --help                 Show help message
```

### Authentication Methods

1. **AWS CLI Profile** (recommended):
   ```bash
   bb --profile my-profile
   ```

2. **Access Keys**:
   ```bash
   bb --access-key AKIAIOSFODNN7EXAMPLE --secret-key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
   ```

3. **Anonymous/Unsigned** (for public buckets):
   ```bash
   bb
   ```

### Interactive Commands

Once in the shell, you can use the following commands:

#### Navigation & Listing
- `ls [-l] [--sort=name|date|size] [path]` - List objects and directories
  - `-l`: Detailed view with size and date
  - `--sort`: Sort by name (default), date, or size
- `cd <path>` - Change directory
- `help` - Show available commands
- `clear` - Clear terminal screen

#### File Operations
- `cat <file>` - Display file contents (uses pager for large files)
- `peek <file> [bytes]` - Peek at first few bytes (default 2KB, max 10MB)
- `open <file>` - Download and open file with system default application
- `get <remote_path> [local_path]` - Download file(s)
  - Supports wildcards: `get "*.txt"` or `get "data/*.csv"`
- `put <local_path> <remote_path>` - Upload local file to remote location

#### Information & Statistics
- `stats` - Display bucket statistics and cached content summary
- `crawlstatus` - Show status of background cache crawling
- `audit` - Audit bucket permissions (placeholder)

#### Shell Control
- `exit` or `quit` - Exit the shell

### Features

#### Multi-Bucket Mode
Run without specifying a bucket to browse all accessible buckets:
```bash
bb
```

#### Tab Completion
- Press TAB to autocomplete commands, file paths, and directory names
- Supports both local and remote path completion

#### Background Operations
- **Stats Collection**: Automatically collects bucket statistics in background
- **Cache Crawling**: Pre-caches directory structure for faster navigation

#### Caching
- Directory listings are cached for 6 hours to improve performance
- Cache is persisted between sessions in `~/.bucketboss_cache/`
- Command history saved in `~/.bucketboss_history`

### Examples

#### Basic Usage
```bash
# Connect to a specific bucket
bb --bucket my-data-bucket

# List files in current directory
ls

# Change to a subdirectory
cd documents/

# View a text file
cat readme.txt

# Download a file
get data.csv ./local_data.csv

# Upload a file
put ./backup.tar.gz backups/
```

#### Advanced Usage
```bash
# List files with details, sorted by date
ls -l --sort=date

# Download all JSON files
get "*.json"

# Peek at first 1KB of a large file
peek large-file.log 1024

# View bucket statistics
stats
```

### Internal Commands

The following commands are internal to bucketboss and provide system functionality:

- **Background Stats Thread**: Collects bucket metadata (location, creation date) without blocking the UI
- **Background Cache Crawler**: Recursively crawls directory structure up to configurable depth for faster navigation
- **Cache Management**: Automatic TTL-based cache invalidation and persistence
- **Path Resolution**: Handles relative/absolute paths and `..` navigation consistently across providers
- **Provider Abstraction**: Clean separation between S3 implementation and the shell interface

### Configuration

Cache TTL and crawl depth can be modified in the source code:
- `CACHE_TTL_SECONDS`: Cache expiration time (default: 6 hours)
- `crawl_depth`: Maximum depth for background crawling (default: 2)


