# BucketBoss — CLI Experience Plan

> **Status:** Draft v2  
> **Date:** 2026-02-09  
> **Audience:** Security researchers exploring open/misconfigured cloud storage  
> **Scope:** Extend BucketBoss with S3 XML/HTTP access, enumeration, secret scanning, and multi-cloud support

---

## 1. Vision

BucketBoss is an **interactive shell for exploring cloud object storage** — built for **security researchers** who routinely encounter open buckets, misconfigured containers, and exposed storage endpoints in the wild. One tool to browse, triage, enumerate, and audit buckets across AWS S3, Google Cloud Storage, Azure Blob Storage, Cloudflare R2, and any S3-compatible endpoint — including raw HTTP/XML access for when you don't have (or want) SDK credentials.

The experience should feel like a natural filesystem shell regardless of which cloud or access method you're using, with built-in features for **reconnaissance**: highlighting juicy files, enumerating sensitive content, and scanning for leaked secrets.

---

## 2. Current State (S3 Support)

### What exists today

| Area | Status |
|---|---|
| **Navigation** — `ls`, `cd`, path resolution, `..` | ✅ Complete |
| **Read** — `cat`, `peek`, `open` | ✅ Complete |
| **Transfer** — `get` (+ wildcards), `put` | ✅ Complete |
| **Info** — `stats`, `crawlstatus` | ✅ Complete |
| **Audit** — `audit` | ⬜ Placeholder only |
| **Multi-bucket mode** | ✅ Complete (no `--bucket` flag) |
| **Authentication** — profile, access keys, unsigned | ✅ Complete |
| **Tab completion** — remote + local paths | ✅ Complete |
| **Caching** — 6hr TTL, disk-persisted, background crawl | ✅ Complete |
| **Provider abstraction** — `CloudProvider` ABC | ✅ Exists (S3Provider, MultiBucketProvider) |

### What's missing or incomplete for S3

1. **`audit` command** — no implementation
2. **`cp`** — no server-side copy
3. **`find` / `grep`** — no search within bucket contents
4. **`tree`** — no visual tree view
5. **`du`** — no disk-usage summary per prefix
6. **`presign`** — no pre-signed URL generation
7. **Bookmarks / aliases** — no way to save frequently used paths
8. **Config file** — all settings are hardcoded or CLI flags

---

## 3. Target Providers

| Provider | Protocol | SDK | Priority |
|---|---|---|---|
| **AWS S3** | S3 API | `boto3` | P0 (existing) |
| **S3 XML/HTTP** | Raw HTTP + S3 XML | `urllib3` / stdlib `urllib` | P0 |
| **Google Cloud Storage** | GCS JSON API | `google-cloud-storage` | P1 |
| **Azure Blob Storage** | Azure Blob API | `azure-storage-blob` | P1 |
| **Cloudflare R2** | S3-compatible API | `boto3` (custom endpoint) | P2 |
| **MinIO / S3-compatible** | S3 API | `boto3` (custom endpoint) | P2 |

> **R2 and MinIO** reuse S3Provider with a custom endpoint URL — minimal new code.

### 3.1 S3 XML/HTTP Provider — Why It Matters

Many open buckets are discovered via URLs like `https://bucket-name.s3.amazonaws.com/` or `https://s3.amazonaws.com/bucket-name/`. These return standard **S3 ListBucketResult XML** and require zero authentication. Security researchers often:

- Don't want to install/configure `boto3` or AWS CLI
- Are working from a minimal recon machine
- Need to browse a bucket found via a URL, not a bucket name + credentials
- Want to avoid any SDK-level credential resolution that might accidentally sign requests

The **S3 XML/HTTP provider** uses pure HTTP requests and parses the S3 XML response format directly. This is the **lightest-weight, zero-auth entry point** into BucketBoss.

**Invocation:**
```bash
# By URL (auto-detects provider as s3xml)
bb --url https://bucket-name.s3.amazonaws.com/
bb --url https://s3.amazonaws.com/bucket-name/
bb --url https://bucket-name.s3.us-west-2.amazonaws.com/

# Explicit provider flag
bb --provider s3xml --bucket bucket-name
bb --provider s3xml --bucket bucket-name --region us-west-2

# Works with any S3-compatible XML endpoint
bb --url https://minio.internal.corp:9000/data-bucket/
```

**How it works:**
- Sends `GET /?list-type=2&delimiter=/&prefix=...` to enumerate
- Parses `<ListBucketResult>` XML (`<CommonPrefixes>`, `<Contents>`)
- Supports pagination via `<NextContinuationToken>` / `continuation-token` param
- `GET` on object keys for `cat`, `peek`, `get`
- `HEAD` for object metadata (`info`)
- Range header for `peek` (`Range: bytes=0-2047`)
- Read-only by default (no `put`, no `dangerously-delete-content` unless `--allow-write` flag is passed)
- Falls back to `list-type=1` (legacy) if `list-type=2` returns an error

---

## 4. CLI Entry Point Design

### 4.1 Invocation Syntax

```
bb [global-options] [provider-options]
```

### 4.2 Global Options (provider-agnostic)

```
bb [--provider s3|gcs|azure|r2] [--bucket BUCKET] [--config FILE] [--no-cache] [--cache-ttl SECONDS] [--crawl-depth N] [--no-crawl] [--verbose] [--version]
```

| Flag | Default | Description |
|---|---|---|
| `--provider` | `s3` | Cloud provider (`s3`, `s3xml`, `gcs`, `azure`, `r2`) |
| `--bucket` | *(none → multi-bucket mode)* | Target bucket/container name |
| `--url` | *(none)* | Direct URL to an open bucket (auto-detects provider, implies `--provider s3xml`) |
| `--config` | `~/.bucketboss/config.json` | Path to config file |
| `--no-cache` | `false` | Disable read cache entirely |
| `--cache-ttl` | `21600` (6hr) | Cache TTL in seconds |
| `--crawl-depth` | `2` | Background crawl depth (0 = disabled) |
| `--no-crawl` | `false` | Disable background crawl |
| `--verbose` | `false` | Show debug/fetch messages on stderr |
| `--version` | — | Print version and exit |

### 4.3 Provider-Specific Options

#### S3 (existing, unchanged)
```
--profile PROFILE         AWS CLI profile name
--access-key KEY          AWS access key
--secret-key SECRET       AWS secret key
--endpoint-url URL        Custom S3 endpoint (for R2, MinIO)
--region REGION           AWS region override
```

#### S3 XML/HTTP (new — zero-dependency)
```
--url URL                 Full URL to open bucket (auto-detects bucket name + region)
                          e.g. https://bucket.s3.amazonaws.com/
                               https://s3.us-west-2.amazonaws.com/bucket/
                               https://custom-s3.internal:9000/bucket/
--allow-write             Enable PUT/DELETE via HTTP (disabled by default for safety)
```

#### GCS (new)
```
--gcs-project PROJECT     GCP project ID
--gcs-credentials FILE    Path to service account JSON key
                          (falls back to ADC / gcloud auth)
```

#### Azure (new)
```
--azure-account ACCOUNT        Storage account name
--azure-key KEY                Storage account key
--azure-connection-string STR  Full connection string
--azure-sas-token TOKEN        SAS token
                               (falls back to DefaultAzureCredential / az login)
```

#### R2 (reuses S3 flags)
```
--r2-account-id ID        Cloudflare account ID (constructs endpoint automatically)
--access-key KEY          R2 access key
--secret-key SECRET       R2 secret key
```

### 4.4 Provider Auto-Detection

If `--provider` is omitted, BucketBoss auto-detects based on flags:
1. Presence of `--url` → **S3 XML/HTTP** (pure HTTP, no SDK)
2. Presence of `--gcs-project` → GCS
3. Presence of `--azure-account` → Azure
4. Presence of `--r2-account-id` → R2
5. Presence of `--endpoint-url` → S3-compatible (via boto3)
6. Default → S3 (via boto3)

---

## 5. Interactive Shell Command Taxonomy

### 5.1 Command Categories

Commands are grouped by function. All commands work identically across providers.

#### Navigation
| Command | Syntax | Description |
|---|---|---|
| `ls` | `ls [-l] [--sort=name\|date\|size] [path]` | List objects and directories |
| `cd` | `cd <path>` | Change directory |
| `tree` | `tree [path] [--depth N]` | Visual tree of directory structure |
| `pwd` | `pwd` | Print current working path (full URI) |

#### Reading
| Command | Syntax | Description |
|---|---|---|
| `cat` | `cat <file>` | Display text file (paged) |
| `peek` | `peek <file> [bytes]` | Preview first N bytes (default 2KB) |
| `head` | `head <file> [lines]` | Show first N lines (default 10) |
| `open` | `open <file>` | Download + open with system app |

#### Transfer
| Command | Syntax | Description |
|---|---|---|
| `get` | `get <remote> [local]` | Download file(s), supports wildcards |
| `put` | `put <local> <remote>` | Upload file |
| `cp` | `cp <src> <dst>` | Server-side copy (within same bucket) |
| `mirror` | `mirror <remote_prefix/> [local_dir]` | Recursively download a directory tree preserving structure |
| `diff` | `diff <file_a> <file_b>` | Compare two files (remote-remote or remote-local) |

#### Deletion (intentionally high-friction)
| Command | Syntax | Description |
|---|---|---|
| `dangerously-delete-content` | `dangerously-delete-content <file_or_directory/>` | Delete a file or recursively delete a directory |

> **Design rationale:** BucketBoss is a **recon-first, read-heavy tool**. Deletion is intentionally named to cause pause — you should never fat-finger a delete when exploring someone else's open bucket. The long command name is the first layer of safety. See §5.5 for full behavior.

#### Search
| Command | Syntax | Description |
|---|---|---|
| `find` | `find <pattern> [--path prefix]` | Find objects by name pattern |
| `grep` | `grep <regex> <file_pattern>` | Search content of text files |

#### Recon & Enumeration
| Command | Syntax | Description |
|---|---|---|
| `enum` | `enum [path] [--depth N] [--download]` | Enumerate and highlight interesting files/directories |
| `enum report` | `enum report [--format text\|json\|md]` | Export last enum results |

#### Secret Scanning
| Command | Syntax | Description |
|---|---|---|
| `th` | `th <file_or_directory/>` | Run TruffleHog on a file or directory (external dep) |
| `th` | `th <file>` | Scan a single remote file for secrets |
| `th` | `th <directory/> [--depth N]` | Recursively scan files in a directory |
| `th status` | `th status` | Check if TruffleHog is installed and show version |

#### Information & Diagnostics
| Command | Syntax | Description |
|---|---|---|
| `stats` | `stats` | Bucket stats + cached content summary |
| `scope` | `scope [path]` | Quick size/count estimate for a bucket or prefix |
| `du` | `du [path] [--depth N]` | Disk usage summary per prefix |
| `info` | `info <file>` | Full object metadata (size, type, etag, etc.) |
| `crawlstatus` | `crawlstatus` | Background crawl progress |
| `presign` | `presign <file> [--expires SECONDS]` | Generate pre-signed/shared URL |

#### Audit & Security
| Command | Syntax | Description |
|---|---|---|
| `audit` | `audit [--type acl\|policy\|public\|all]` | Bucket security audit |
| `audit acl` | `audit acl [path]` | Show ACL on bucket or object |
| `audit policy` | `audit policy` | Show bucket policy |
| `audit public` | `audit public` | Check public access settings |
| `audit encryption` | `audit encryption` | Check encryption configuration |
| `audit versioning` | `audit versioning` | Check versioning status |
| `audit lifecycle` | `audit lifecycle` | Show lifecycle rules |
| `audit cors` | `audit cors` | Show CORS configuration |
| `audit logging` | `audit logging` | Check access logging config |

#### Findings & Reporting
| Command | Syntax | Description |
|---|---|---|
| `tag` | `tag <file> "note"` | Annotate a file with a free-text finding |
| `findings` | `findings [--severity X] [--source X]` | View all findings (enum + th + tags) |
| `export` | `export [--format text\|json\|md]` | Generate assessment report |

#### Session Management
| Command | Syntax | Description |
|---|---|---|
| `session save` | `session save [name]` | Save current investigation state |
| `session list` | `session list` | List saved sessions |
| `session load` | `session load <name>` | Resume a saved session |
| `session delete` | `session delete <name>` | Delete a saved session |

#### Shell / UX
| Command | Syntax | Description |
|---|---|---|
| `help` | `help [command]` | Show help (general or per-command) |
| `clear` | `clear` | Clear terminal |
| `history` | `history [N]` | Show recent command history |
| `set` | `set <key> <value>` | Change runtime settings |
| `exit` / `quit` | `exit` | Exit shell (auto-saves session) |

### 5.2 Safety Rules for Destructive Operations

BucketBoss has exactly **one** destructive command: `dangerously-delete-content`. It must:

1. Print a full inventory of what will be deleted (files, count, total size)
2. For directories, require `--recursive` flag explicitly — no implicit recursive delete
3. Require the user to type the bucket name (or a confirmation phrase) to proceed — not just `y`
4. Never support a `--yes` / `-y` skip flag — **always interactive**
5. Not be tab-completable (excluded from completer to prevent accidental invocation)

### 5.5 `dangerously-delete-content` (Deep Dive)

The only destructive command in BucketBoss. Named to make you think twice.

#### Syntax

```
dangerously-delete-content <file>
dangerously-delete-content <directory/> --recursive
```

#### Single file deletion

```
s3://exposed-bucket/data/> dangerously-delete-content old-report.csv

⚠️  DELETE: old-report.csv (14.2 KB)
   Bucket: exposed-bucket
   Full key: data/old-report.csv

Type the bucket name to confirm deletion: exposed-bucket
✅ Deleted: data/old-report.csv
```

#### Recursive directory deletion

```
s3://exposed-bucket/> dangerously-delete-content temp/ --recursive

⚠️  RECURSIVE DELETE: temp/
   Bucket: exposed-bucket
   Objects: 47 files across 6 subdirectories
   Total size: 128.4 MB

   🔴 This includes potentially sensitive files:
      temp/config/.env (312 B)
      temp/keys/deploy.pem (1.7 KB)

Type the bucket name to confirm deletion: exposed-bucket
Deleting... 47/47 objects removed.
✅ Deleted: temp/ (47 objects, 128.4 MB)
```

#### Safety features

- **No `--recursive` flag, no directory deletion** — `dangerously-delete-content temp/` without `--recursive` prints an error and does nothing
- **Bucket name confirmation** — not `y/n`, you must type the actual bucket name
- **Enum-aware warnings** — if the target contains 🔴/🟠 files (per enum rules), they're highlighted before confirmation
- **Not in completer** — tab completion will never suggest this command
- **Logged** — every deletion is logged to `~/.bucketboss/delete.log` with timestamp, bucket, key, and size

### 5.3 `enum` — Enumeration & Triage (Deep Dive)

The `enum` command is the **primary recon tool** inside BucketBoss. It crawls a path (or the whole bucket), classifies every object it finds, and produces a prioritized report of what a security researcher should look at first.

#### How it works

1. Recursively lists all objects under the target path (up to `--depth`, default 5)
2. Classifies each file by name, extension, path context, and size
3. Produces a **tiered output**: 🔴 Critical → 🟠 High → 🟡 Medium → 🔵 Info

#### Classification Rules

**🔴 Critical — Likely secrets/credentials (look at these first)**

| Pattern | Why |
|---|---|
| `.env`, `.env.*` | Environment variables, often contains API keys, DB passwords |
| `credentials`, `credentials.json`, `credentials.xml` | Cloud service account keys |
| `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks` | Private keys, certificates, keystores |
| `id_rsa`, `id_ed25519`, `*.ppk` | SSH private keys |
| `*.kdbx`, `*.kdb` | KeePass databases |
| `htpasswd`, `.htpasswd` | Apache password files |
| `shadow`, `passwd` (not in obvious OS paths) | System credential files |
| `wp-config.php` | WordPress config (DB creds) |
| `database.yml`, `database.json` | Database connection configs |
| `secrets.yml`, `secrets.json`, `secrets.toml` | Generic secret files |
| `*token*`, `*secret*`, `*password*` in filename | Obvious secret-related filenames |
| `.npmrc`, `.pypirc`, `.docker/config.json` | Package registry / Docker auth tokens |
| `.git-credentials`, `.netrc` | Git/network credential stores |
| `service-account*.json` | GCP service account keys |
| `terraform.tfstate`, `*.tfvars` | Terraform state (contains all infra secrets) |
| `vault-token`, `vault.json` | HashiCorp Vault tokens |

**🟠 High — Configuration & infrastructure (often contains embedded secrets)**

| Pattern | Why |
|---|---|
| `*.conf`, `*.cfg`, `*.ini`, `*.config` | Configuration files |
| `*.yml`, `*.yaml`, `*.toml` | Structured config (K8s, CI/CD, app config) |
| `docker-compose*.yml` | Docker configs with env vars / passwords |
| `Dockerfile*` | May contain hardcoded tokens in build args |
| `*.sql`, `*.dump`, `*.bak` | Database dumps — may contain entire datasets |
| `*.sqlite`, `*.db`, `*.mdb` | Database files |
| `*.csv`, `*.xlsx` with PII-like names | Spreadsheets (customer data, exports) |
| `.git/` directory | Exposed git repo — full source + history |
| `backup/`, `bak/`, `old/`, `archive/` directories | Backup directories often contain stale secrets |
| `debug/`, `test/`, `staging/`, `dev/` directories | Non-production paths with weaker security |
| `Jenkinsfile`, `.github/workflows/*.yml` | CI/CD pipeline definitions |
| `kubeconfig`, `kube/config` | Kubernetes cluster credentials |
| `ansible/`, `playbook*.yml` | Ansible with potential vault passwords |

**🟡 Medium — Interesting for deeper inspection**

| Pattern | Why |
|---|---|
| `*.log`, `*.log.*` | Log files — may contain tokens, IPs, errors |
| `*.xml` | Config/data files |
| `*.json` (generic) | Data/config — worth a peek |
| `*.sh`, `*.bash`, `*.ps1`, `*.bat` | Scripts — may contain hardcoded values |
| `*.py`, `*.js`, `*.rb`, `*.php` | Source code |
| `*.zip`, `*.tar.gz`, `*.7z`, `*.rar` | Archives — may contain any of the above |
| `*.war`, `*.jar`, `*.ear` | Java packages — may contain config |
| `README*`, `CHANGELOG*`, `TODO*` | Documentation — reveals project structure |

**🔵 Info — Probably benign but noted**

| Pattern | Why |
|---|---|
| `*.jpg`, `*.png`, `*.gif`, `*.svg`, `*.mp4` | Media files |
| `*.css`, `*.html` (static assets) | Frontend assets |
| `*.woff`, `*.ttf`, `*.eot` | Font files |
| `node_modules/`, `vendor/`, `__pycache__/` | Dependency directories |

#### Example Output

```
s3://exposed-bucket/> enum --depth 3

🔍 Enumerating exposed-bucket/ (depth: 3)...
   Scanned: 1,247 objects across 83 directories

🔴 CRITICAL (7 files) — likely secrets, inspect immediately
   .env                                           312 B   2024-03-15
   config/credentials.json                       1.2 KB   2024-06-01
   keys/production.pem                           1.7 KB   2023-11-20
   keys/id_rsa                                   2.4 KB   2023-11-20
   deploy/terraform.tfstate                    245.8 KB   2024-08-12
   backups/db-prod-2024-01.sql.gz               18.3 MB   2024-01-31
   .git-credentials                               89 B   2023-09-05

🟠 HIGH (23 files) — config & infra, often contains embedded secrets
   docker-compose.yml                            2.1 KB   2024-07-20
   .github/workflows/deploy.yml                 1.8 KB   2024-08-01
   config/database.yml                             845 B   2024-06-01
   config/app.conf                               3.2 KB   2024-06-01
   ...and 19 more (use 'enum report' for full list)

🟡 MEDIUM (89 files) — worth a closer look
   logs/app-2024-08.log                         42.1 MB   2024-08-31
   scripts/deploy.sh                             1.4 KB   2024-07-15
   src/config.py                                 2.8 KB   2024-08-10
   ...and 86 more

🔵 INFO (1,128 files) — media, assets, dependencies
   (Omitted — use 'enum report --format json' for complete inventory)

📊 Summary:
   Total: 1,247 objects | 892.4 MB
   Directories with interesting names: backup/ (🟠), .git/ (🟠), keys/ (🔴), config/ (🟠)

💡 Suggested next steps:
   cat .env
   peek config/credentials.json
   th config/                          # scan config/ for secrets with TruffleHog
   get keys/id_rsa                     # download for offline analysis
```

#### `enum` Options

| Flag | Default | Description |
|---|---|---|
| `--depth N` | `5` | Max directory depth to crawl |
| `--download` | `false` | Auto-download all 🔴 CRITICAL files to `./bb-enum-<bucket>/` |
| `--format text\|json\|md` | `text` | Output format (json is machine-parseable) |
| `--min-severity critical\|high\|medium\|info` | `info` | Only show files at or above this severity |
| `--no-classify` | `false` | Just list everything flat (raw enumeration) |

#### Directory Name Highlighting

`enum` also flags **directory names** that are interesting regardless of contents:

| Directory Pattern | Flag |
|---|---|
| `.git/`, `.svn/`, `.hg/` | 🔴 Source control — full repo exposed |
| `backup/`, `bak/`, `old/`, `archive/`, `dump/` | 🟠 Stale data |
| `admin/`, `internal/`, `private/`, `restricted/` | 🟠 Should not be public |
| `keys/`, `certs/`, `secrets/`, `credentials/` | 🔴 Named for sensitive content |
| `logs/`, `debug/`, `tmp/`, `temp/` | 🟡 Operational data |
| `config/`, `conf/`, `settings/`, `etc/` | 🟠 Configuration |
| `.well-known/` | 🟡 May reveal infrastructure |

### 5.4 `th` — TruffleHog Integration (Deep Dive)

The `th` command integrates [TruffleHog](https://github.com/trufflesecurity/trufflehog) as an **external secret scanner** that runs against remote files without requiring the user to manually download them first.

#### Prerequisites

- TruffleHog must be installed separately by the user (`trufflehog` binary in PATH)
- BucketBoss does NOT bundle or install TruffleHog — it's an external dependency
- On first use (or via `th status`), BucketBoss checks for availability

```
s3://bucket/> th status

✅ TruffleHog found: /usr/local/bin/trufflehog (v3.82.2)
   Detection: filesystem mode will be used
   Temp directory: /tmp/bb-th-scans/
```

```
s3://bucket/> th status

❌ TruffleHog not found in PATH.
   ↳ Install: https://github.com/trufflesecurity/trufflehog#installation
   ↳ brew install trufflehog
   ↳ pip install trufflehog  (Python version)
```

#### How it works

1. **Single file:** `th config/secrets.json`
   - Downloads the file to a temp directory
   - Runs `trufflehog filesystem --directory <temp_dir> --json`
   - Parses JSON output and presents findings
   - Cleans up temp file

2. **Directory:** `th config/`
   - Downloads all files under `config/` to a temp directory (respecting `--depth`)
   - Runs TruffleHog against the entire temp directory
   - Maps findings back to remote paths
   - Cleans up

3. **With depth control:** `th data/ --depth 2`
   - Only scans files up to 2 levels deep under `data/`

#### Example Session

```
s3://exposed-bucket/> th .env

🔍 Scanning .env with TruffleHog...
   Downloaded: .env (312 B)

🚨 TruffleHog Results (3 findings):

  1. AWS Access Key
     Detector:  AWS
     Verified:  ✅ Yes (key is ACTIVE)
     File:      .env
     Line:      3
     Secret:    AKIA...REDACTED

  2. Database Password
     Detector:  Generic High Entropy
     Verified:  ❓ Unknown
     File:      .env
     Line:      7
     Secret:    postgres://user:p4$$...REDACTED@db.host/prod

  3. Slack Webhook
     Detector:  SlackWebhook
     Verified:  ✅ Yes (webhook is active)
     File:      .env
     Line:      12
     Secret:    https://hooks.slack.com/services/T.../B.../REDACTED

🧹 Cleaned up temp files.
```

```
s3://exposed-bucket/> th config/ --depth 1

🔍 Scanning config/ with TruffleHog (depth: 1)...
   Downloaded: 4 files (8.2 KB total)

🚨 TruffleHog Results (1 finding):

  1. GCP Service Account Key
     Detector:  GCP
     Verified:  ✅ Yes
     File:      config/credentials.json  (remote: config/credentials.json)
     Secret:    {"type": "service_account", "project_id": ...REDACTED}

✅ No findings in: config/app.conf, config/nginx.conf, config/routes.yml
🧹 Cleaned up temp files.
```

#### `th` Options

| Flag | Default | Description |
|---|---|---|
| `--depth N` | `3` | Max depth when scanning a directory |
| `--verified-only` | `false` | Only show verified (active) secrets |
| `--json` | `false` | Raw JSON output from TruffleHog |
| `--keep` | `false` | Don't delete temp files after scan |
| `--max-size SIZE` | `50MB` | Skip files larger than this |

#### Safety & Cleanup

- All downloaded files go to a dedicated temp directory (`/tmp/bb-th-scans/` or OS temp)
- Temp directory is cleaned up after each scan (unless `--keep`)
- Large files (>50MB default) are skipped with a warning
- Binary files are included (TruffleHog handles them)
- No secrets are logged or cached by BucketBoss itself

### 5.6 Connection Permission Probing

On connect, BucketBoss silently probes what operations the current credentials (or lack thereof) allow. This runs in the background and populates the startup banner.

#### Probes performed

| Probe | How | Detects |
|---|---|---|
| **List** | `GET /?list-type=2&max-keys=1` | Can enumerate objects |
| **Read** | `HEAD` on first listed object | Can download/read files |
| **Write** | `PUT` a zero-byte `.bb-probe-test` then `DELETE` it | Can upload (only if `--probe-write` flag set, skipped by default) |
| **ACL** | `GET ?acl` | Can read bucket ACL |
| **Policy** | `GET ?policy` | Can read bucket policy |

#### Startup banner

```
$ bb --url https://exposed-bucket.s3.amazonaws.com/

🪣 BucketBoss v0.2.0
   Target:    s3://exposed-bucket/
   Transport: HTTP/XML (unsigned)
   Region:    us-east-1 (from endpoint)
   Access:    ✅ List  ✅ Read  ⬜ Write (not probed)  ✅ ACL  ❌ Policy
   Objects:   ~12,400 (estimated from first page)

   💡 Try: enum --depth 2

s3://exposed-bucket/>
```

- Write probing is **off by default** (don't write to someone else's bucket during recon)
- The object count estimate comes from `<KeyCount>` in the first list response
- If List fails, the tool exits with a clear error — can't do anything without list access

### 5.7 `scope` — Quick Bucket Survey (Deep Dive)

`scope` gives you a fast answer to "how big is this bucket?" without waiting for a full `enum` or `du` crawl. It's the first thing you run to decide how deep to go.

#### How it works

1. Fetches object listing **without delimiter** (flat list, no directory grouping)
2. Streams through all objects counting and summing sizes
3. Samples file extensions and path patterns as it goes
4. Prints running progress and a summary when done (or when interrupted with `q`)

```
s3://exposed-bucket/> scope

🔭 Scanning bucket... (press 'q' to stop early)
   Scanned: 12,847 objects (892.4 MB) across ~247 prefixes...

📊 Scope Results:
   Total objects:  12,847
   Total size:     892.4 MB
   Deepest path:   data/exports/2024/q3/monthly/ (6 levels)
   Top extensions: .json (4,201) .csv (3,812) .log (1,443) .png (891)
   Top prefixes:   data/ (8,102) logs/ (2,341) assets/ (1,204)
   Oldest object:  2023-03-14 (data/seed/init.sql)
   Newest object:  2026-02-09 (logs/app-current.log)

   💡 Try: enum --depth 3   (for security-relevant triage)
```

```
s3://exposed-bucket/> scope data/exports/

🔭 Scanning data/exports/... (press 'q' to stop early)

📊 Scope Results (data/exports/):
   Total objects:  3,812
   Total size:     245.1 MB
   ...
```

### 5.8 `mirror` — Recursive Download (Deep Dive)

Downloads an entire remote prefix to local disk, preserving directory structure. The primary use case is "grab everything for offline analysis."

#### Syntax

```
mirror <remote_prefix/> [local_dir]
mirror config/ ./loot/config/
mirror . ./full-bucket-dump/
```

#### Behavior

```
s3://exposed-bucket/> mirror config/ ./loot/

🪞 Mirror: s3://exposed-bucket/config/ → ./loot/config/
   Files: 12 (total 34.8 KB)

   config/app.conf              3.2 KB  ✅
   config/database.yml            845 B  ✅
   config/credentials.json      1.2 KB  ✅
   config/.env                    312 B  ✅
   config/nginx/default.conf    1.1 KB  ✅
   ...
   12/12 complete (34.8 KB)

✅ Mirrored to ./loot/config/
```

#### Options

| Flag | Default | Description |
|---|---|---|
| `--depth N` | unlimited | Max directory depth |
| `--max-size SIZE` | `100MB` | Skip files larger than this |
| `--dry-run` | `false` | Show what would be downloaded without downloading |
| `--include PATTERN` | `*` | Only download files matching glob |
| `--exclude PATTERN` | *(none)* | Skip files matching glob |
| `--flat` | `false` | Download all files into a single directory (no subdirs) |

### 5.9 `diff` — File Comparison (Deep Dive)

Compare two files — both remote, or one remote and one local. Useful when you find duplicate config files across directories and want to spot differences.

#### Syntax

```
diff <file_a> <file_b>          # both remote (relative to current prefix)
diff <remote_file> ~/local/file # remote vs local (local path detected by ./ or ~/ or /)
```

#### Behavior

1. Downloads both files to temp directory
2. Runs unified diff (like `diff -u`)
3. Colorizes output: green for additions, red for deletions
4. Cleans up temp files

```
s3://exposed-bucket/> diff config/app.conf config/app.conf.bak

--- config/app.conf        (2026-02-09, 3.2 KB)
+++ config/app.conf.bak    (2024-11-20, 3.1 KB)
@@ -12,3 +12,3 @@
 db_host = prod-db.internal
-db_password = n3w-$ecure-P@ss!
+db_password = old-password-123
 db_port = 5432
```

For binary files, shows a size/hash comparison instead of content diff.

### 5.10 BB Sessions — Auto-Save & Resume (Deep Dive)

A **session** captures your entire investigation state for a bucket so you can resume later or hand off to a teammate. Sessions are local-only and never uploaded.

#### What's saved in a session

| Data | Description |
|---|---|
| Connection info | Provider, bucket, URL, auth method used |
| Current path | Where you left off (`cd` state) |
| Cache snapshot | All cached directory listings |
| Enum results | Last `enum` output (if run) |
| Findings | All `tag` annotations and `th` scan results |
| Command history | Shell history for this session |
| Timestamp | When the session was created/last updated |

#### Storage

```
~/.bucketboss/sessions/
├── exposed-bucket_2026-02-09T14-30-00.bbsession.json
├── client-data-leak_2026-02-08T09-15-00.bbsession.json
└── ...
```

#### Session commands

| Command | Syntax | Description |
|---|---|---|
| `session save` | `session save [name]` | Save current session (auto-named if omitted) |
| `session list` | `session list` | List all saved sessions |
| `session load` | `session load <name_or_index>` | Resume a saved session |
| `session delete` | `session delete <name_or_index>` | Delete a saved session |

#### Auto-save behavior

- Sessions are **auto-saved on exit** (`exit` / `quit` / Ctrl-D)
- On next `bb` invocation against the same bucket, BucketBoss asks:
  ```
  Found previous session for s3://exposed-bucket/ (2026-02-09 14:30)
  Resume? [Y/n]:
  ```
- `--no-session` flag skips session restore and starts fresh

### 5.11 Findings Pipeline — `tag`, `findings`, `export` (Deep Dive)

This is the system that ties `enum`, `th`, and manual annotations together into a **unified findings log** for the current session. Think of it as your investigation notebook.

#### How findings accumulate

```
 ┌─────────┐     ┌──────────┐     ┌──────────────┐
 │  enum    │────▶│          │     │              │
 │ (auto)   │     │ Findings │────▶│   export     │
 │          │     │   Log    │     │ (report out) │
 └─────────┘     │          │     └──────────────┘
 ┌─────────┐     │          │
 │  th     │────▶│          │
 │ (auto)   │     │          │
 └─────────┘     │          │
 ┌─────────┐     │          │
 │  tag    │────▶│          │
 │ (manual) │     │          │
 └─────────┘     └──────────┘
```

**Automatic sources:**
- `enum` auto-tags every file it classifies at 🔴 or 🟠 severity
- `th` auto-tags every file where TruffleHog finds a secret (with detector type + verified status)

**Manual source:**
- `tag` lets you annotate any file with a free-text note

#### `tag` — Manual Annotation

```
s3://exposed-bucket/> tag .env "Confirmed active AWS keys — AKIA... has S3FullAccess"

✏️  Tagged: .env
   Note: Confirmed active AWS keys — AKIA... has S3FullAccess
```

```
s3://exposed-bucket/> tag config/database.yml "Contains prod DB password, matches RDS instance in us-east-1"

✏️  Tagged: config/database.yml
   Note: Contains prod DB password, matches RDS instance in us-east-1
```

Tags are attached to the **remote path** and stored in the session. Multiple tags can be added to the same file.

#### `findings` — View Current Investigation State

```
s3://exposed-bucket/> findings

📋 Findings for s3://exposed-bucket/ (12 items)

 #   Severity  Source   Path                        Summary
 1   🔴 CRIT   enum     .env                        Environment file (likely secrets)
 2   🔴 CRIT   th       .env                        AWS Access Key (VERIFIED ✅)
 3   🔴 CRIT   th       .env                        Slack Webhook (VERIFIED ✅)
 4   🔴 CRIT   tag      .env                        "Confirmed active AWS keys — AKIA... has S3FullAccess"
 5   🔴 CRIT   enum     keys/id_rsa                 SSH private key
 6   🔴 CRIT   enum     deploy/terraform.tfstate     Terraform state file
 7   🔴 CRIT   th       config/credentials.json      GCP Service Account Key (VERIFIED ✅)
 8   🟠 HIGH   enum     docker-compose.yml           Docker config (may contain secrets)
 9   🟠 HIGH   enum     .github/workflows/deploy.yml CI/CD pipeline
10   🟠 HIGH   tag      config/database.yml          "Contains prod DB password, matches RDS instance"
11   🟡 MED    enum     logs/app-2024-08.log         Log file
12   🔵 INFO   tag      README.md                    "Internal project — WidgetCorp, team: platform-eng"
```

#### `findings` Options

| Flag | Default | Description |
|---|---|---|
| `--severity critical\|high\|medium\|info` | all | Filter by minimum severity |
| `--source enum\|th\|tag` | all | Filter by source |
| `--json` | `false` | Output as JSON (for piping) |

#### `export` — Generate Assessment Report

```
s3://exposed-bucket/> export

📄 Export format? [text/json/md] (default: md): md

Exported to: ./bb-report-exposed-bucket-2026-02-09.md
   Findings:    12
   Files tagged: 5
   TH scans:    2 (5 secrets found, 3 verified)
   Enum scope:  1,247 objects across 83 directories
```

The exported report includes:
- **Header:** Bucket info, connection method, access permissions, timestamps
- **Findings table:** All findings sorted by severity
- **Enum summary:** Full classification breakdown
- **TruffleHog results:** All scan results with detector types
- **Manual notes:** All `tag` annotations
- **Scope data:** Bucket size, object count, age range
- **Appendix:** Raw file listing of 🔴/🟠 classified files

This is the artifact you attach to a security assessment or bug bounty report.

---

## 6. Provider Abstraction Extensions

### 6.1 New Methods on `CloudProvider` ABC

```python
class CloudProvider(ABC):
    # ... existing methods ...

    @abstractmethod
    def delete_object(self, key: str) -> None:
        """Delete a single object. Called only by dangerously-delete-content."""
        ...

    @abstractmethod
    def delete_objects(self, keys: list[str]) -> dict:
        """Batch delete objects. Returns {"deleted": [...], "errors": [...]}."""
        ...

    @abstractmethod
    def copy_object(self, src_key: str, dst_key: str) -> None: ...

    @abstractmethod
    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str: ...

    @abstractmethod
    def get_bucket_acl(self) -> dict: ...

    @abstractmethod
    def get_bucket_policy(self) -> Optional[dict]: ...

    @abstractmethod
    def get_public_access_config(self) -> dict: ...

    @abstractmethod
    def get_encryption_config(self) -> Optional[dict]: ...

    @abstractmethod
    def get_versioning_config(self) -> dict: ...

    @abstractmethod
    def get_lifecycle_rules(self) -> list: ...

    @abstractmethod
    def get_cors_config(self) -> list: ...

    @abstractmethod
    def get_logging_config(self) -> Optional[dict]: ...

    @abstractmethod
    def get_object_acl(self, key: str) -> dict: ...
```

> Not every provider supports every operation. Methods should raise `NotImplementedError` with a clear message for unsupported features (e.g., GCS doesn't have bucket policies in the same way S3 does).

### 6.2 Provider URI Scheme

Each provider uses a canonical URI prefix for its prompt:

| Provider | Prompt Format |
|---|---|
| S3 | `s3://bucket-name/path/> ` |
| S3 XML | `s3://bucket-name/path/> ` (same scheme — it's still S3, just different transport) |
| GCS | `gs://bucket-name/path/> ` |
| Azure | `az://container-name/path/> ` |
| R2 | `r2://bucket-name/path/> ` |

> The S3 XML provider uses the same `s3://` prompt scheme since it's the same logical storage — the difference is transport (raw HTTP vs boto3 SDK). The connection method is shown in the startup banner.

---

## 7. Configuration File

### 7.1 Location

`~/.bucketboss/config.json` (created on first run with defaults)

> **Why JSON?** Structurally unambiguous, no indentation semantics, native to Python stdlib (`json` module), and familiar to security researchers who deal with JSON payloads constantly.

### 7.2 Schema

```json
{
  "general": {
    "default_provider": "s3",
    "cache_ttl": 21600,
    "crawl_depth": 2,
    "verbose": false,
    "delete_log": "~/.bucketboss/delete.log"
  },
  "s3": {
    "default_profile": "default",
    "default_region": "us-east-1",
    "endpoints": {
      "minio_local": "http://localhost:9000",
      "r2_prod": "https://<account-id>.r2.cloudflarestorage.com"
    }
  },
  "gcs": {
    "default_project": "my-project"
  },
  "azure": {
    "default_account": "mystorageaccount"
  }
}
```

---

## 8. UX Patterns

### 8.1 Prompt Design

```
s3://my-bucket/data/2026/> ls
gs://media-assets/images/> cd ..
az://backups/> get latest.tar.gz
r2://cdn-cache/> stats
```

The prompt always shows `<scheme>://<bucket>/<current_path>>`.  
In multi-bucket mode: `s3://>` (no bucket selected).

### 8.2 Output Formatting

- **Icons:** Keep emoji file-type icons (📁📄🐍🖼📦 etc.) on non-Windows
- **Colors:** Add ANSI color support via prompt_toolkit styles:
  - Directories → **bold blue**
  - Executables/scripts → **green**
  - Archives → **red**
  - Images → **magenta**
- **`ls -l` columnar output:** Align columns consistently
  ```
  📁 data/
  📁 logs/
  🐍 2026-01-15 14:30   4.2 KB  pipeline.py
  📦 2026-02-01 09:15  12.8 MB  backup.tar.gz
  📄 2026-02-09 11:00    256 B  config.yaml
  ```

### 8.3 Error Presentation

```
Error: Access denied to s3://my-bucket/secret/
  ↳ Check IAM permissions for s3:GetObject on this prefix.
  ↳ Try: bb --profile admin-profile --bucket my-bucket
```

Actionable error messages with hints. Never just print raw exception traces.

### 8.4 Progress Indicators

For long operations (`get`, `put`, `sync`, `du` on large prefixes):
- Spinner + status text for indeterminate operations
- Progress bar (using prompt_toolkit or simple ASCII) for file transfers:
  ```
  Downloading backup.tar.gz  [████████░░░░░░░░]  52%  6.4/12.8 MB  2.1 MB/s
  ```

### 8.5 Tab Completion Enhancements

- **Command-aware:** Only show relevant completions per command
- **Fuzzy matching:** Allow partial matches (e.g., `cd da` → `data/`)
- **Multi-level:** Complete through directory separators (`cd data/20` → `data/2026/`)
- **Help hints:** Show brief description next to command completions
  ```
  > st<TAB>
  stats        Display bucket statistics
  ```

### 8.6 Help System

`help` alone shows the grouped command list. `help <command>` shows detailed usage:

```
> help get

  get <remote_path> [local_path]

  Download a file from the current bucket to your local machine.

  Arguments:
    remote_path   Remote file path (relative to current directory)
    local_path    Local destination (default: current working directory)

  Wildcards:
    get "*.csv"           Download all CSV files in current directory
    get "data/*.json"     Download all JSON files in data/

  Examples:
    get report.pdf
    get report.pdf ~/Downloads/
    get "logs/*.log" /tmp/logs/
```

---

## 9. Non-Interactive Mode (Future)

Support one-shot commands for scripting:

```bash
# Single command execution
bb --provider s3 --bucket my-bucket -- ls -l data/
bb --provider gcs --bucket assets -- get "images/*.png" ./local/
bb --provider s3 --bucket logs -- cat app/error.log

# Pipe-friendly (no icons, no colors when stdout is not a tty)
bb --bucket logs -- ls data/ | grep ".csv" | wc -l
```

The `--` separates bb flags from the shell command. When stdout is not a TTY, BucketBoss automatically:
- Disables emoji icons
- Disables ANSI colors
- Outputs plain text suitable for piping

---

## 10. Implementation Roadmap

### Phase 1 — Core Recon Experience (Foundation)
> Build the features that matter most to security researchers exploring open buckets.

| # | Task | Effort | Priority |
|---|---|---|---|
| 1.1 | **S3 XML/HTTP Provider** — pure HTTP, parse ListBucketResult XML, `--url` flag | Large | P0 |
| 1.2 | **`enum` command** — recursive enumeration with severity classification (🔴🟠🟡🔵) | Large | P0 |
| 1.3 | **`th` command** — TruffleHog integration (file + directory scanning) | Medium | P0 |
| 1.4 | **Connection permission probing** — auto-detect List/Read/ACL/Policy access on connect | Medium | P0 |
| 1.5 | **`scope`** — quick bucket survey (object count, size, top extensions, age range) | Medium | P0 |
| 1.6 | **`tag` / `findings` / `export`** — findings pipeline with auto-feed from enum + th | Large | P0 |
| 1.7 | `info <file>` — full object metadata (size, content-type, etag, last-modified) | Small | P0 |
| 1.8 | `pwd` command | Trivial | P0 |
| 1.9 | `head <file> [lines]` | Small | P0 |
| 1.10 | **`mirror`** — recursive download preserving directory structure | Medium | P1 |
| 1.11 | **`diff`** — compare two files (remote-remote or remote-local) | Medium | P1 |
| 1.12 | **`session`** — auto-save/resume investigation state | Medium | P1 |
| 1.13 | `find <pattern>` — name search using cache + live fetch | Medium | P1 |
| 1.14 | `du [path]` — recursive size summary | Medium | P1 |
| 1.15 | `tree [path] [--depth N]` | Medium | P1 |
| 1.16 | `audit` — full implementation (ACL, policy, public, encryption, versioning, lifecycle) | Large | P1 |
| 1.17 | `help <command>` — per-command detailed help | Medium | P1 |
| 1.18 | `cp` — server-side copy | Small | P2 |
| 1.19 | `presign <file>` — generate pre-signed URLs | Small | P2 |
| 1.20 | `dangerously-delete-content` — high-friction deletion with bucket-name confirmation | Medium | P2 |
| 1.21 | `history [N]` command | Trivial | P2 |

### Phase 2 — Architecture Refactor
> Prepare the codebase for multi-provider, multi-file structure.

| # | Task | Effort | Priority |
|---|---|---|---|
| 2.1 | Split `bucketboss.py` into modules: `cli.py`, `app.py`, `providers/`, `cache.py`, `completer.py`, `commands/` | Large | P0 |
| 2.2 | Config file support (`~/.bucketboss/config.json`) | Medium | P0 |
| 2.3 | `--endpoint-url` flag for S3-compatible services | Small | P0 |
| 2.4 | Extend `CloudProvider` ABC with new abstract methods | Medium | P0 |
| 2.5 | Provider registry / factory pattern for instantiation | Medium | P0 |
| 2.6 | Cache keying per provider+bucket (not just bucket name) | Small | P1 |
| 2.7 | ANSI color support for output | Medium | P1 |
| 2.8 | Progress bar for transfers | Medium | P2 |

### Phase 3 — Multi-Cloud Providers
> Add new providers. **Starts after** S3 XML/HTTP provider and core recon features (Phase 1 P0s) are solid.

| # | Task | Effort | Priority |
|---|---|---|---|
| 3.1 | **R2 Provider** — reuse S3Provider with custom endpoint + `--r2-account-id` convenience flag | Small | P0 |
| 3.2 | **MinIO** — test + document S3Provider with `--endpoint-url` | Small | P0 |
| 3.3 | **GCS Provider** — `GCSProvider(CloudProvider)` using `google-cloud-storage` | Large | P1 |
| 3.4 | **Azure Provider** — `AzureBlobProvider(CloudProvider)` using `azure-storage-blob` | Large | P1 |
| 3.5 | Multi-provider mode (browse across clouds in one session) | Large | P2 |

### Phase 4 — Advanced Recon Features
> Power-user features for security research workflows.

| # | Task | Effort | Priority |
|---|---|---|---|
| 4.1 | Non-interactive / one-shot command mode (`bb --url ... -- enum`) | Medium | P1 |
| 4.2 | `grep` — content search across text files | Large | P2 |
| 4.3 | `export` — full bucket report (structure + enum + findings) for assessments | Medium | P2 |
| 4.4 | Output format flags (`--json`, `--csv`) for scripting | Medium | P2 |
| 4.5 | Auto-`enum --depth 1` on connect (opt-in) | Small | P2 |
| 4.6 | `cat`/`peek` secret highlighting (regex-based inline markers) | Medium | P3 |
| 4.7 | Plugin system for custom providers | Large | P3 |

---

## 11. Dependency Strategy

| Component | Dependency | Install Strategy |
|---|---|---|
| S3 | `boto3` | Already present |
| **S3 XML/HTTP** | **None** — stdlib `urllib.request` + `xml.etree` | Built-in, zero-dependency |
| GCS | `google-cloud-storage` | Optional extra: `pip install bucketboss[gcs]` |
| Azure | `azure-storage-blob`, `azure-identity` | Optional extra: `pip install bucketboss[azure]` |
| R2 / MinIO | *(none — reuses boto3)* | — |
| All | `prompt_toolkit` | Already present |
| **TruffleHog** | `trufflehog` binary | **External runtime dep** — user installs separately |

`pyproject.toml` optional dependencies:
```toml
[project.optional-dependencies]
gcs = ["google-cloud-storage"]
azure = ["azure-storage-blob", "azure-identity"]
all = ["google-cloud-storage", "azure-storage-blob", "azure-identity"]
```

Missing provider SDKs produce a clear error at connection time:
```
Error: GCS support requires 'google-cloud-storage'.
  ↳ Install with: pip install bucketboss[gcs]
```

TruffleHog is checked at runtime when `th` is invoked:
```
Error: TruffleHog not found in PATH.
  ↳ Install: brew install trufflehog
  ↳ Or: https://github.com/trufflesecurity/trufflehog#installation
```

---

## 12. File Structure (Post-Refactor)

```
bucketboss/
├── __init__.py
├── __main__.py              # entry point
├── cli.py                   # argparse, config loading
├── app.py                   # BucketBossApp (shell loop, command dispatch)
├── completer.py             # BucketBossCompleter
├── cache.py                 # Cache manager (TTL, persistence, crawl)
├── formatting.py            # Icons, colors, human_readable_size
├── config.py                # Config file parsing
├── providers/
│   ├── __init__.py          # Provider registry + factory
│   ├── base.py              # CloudProvider ABC
│   ├── s3.py                # S3Provider, MultiBucketProvider
│   ├── s3xml.py             # S3XMLProvider — pure HTTP/XML, zero SDK
│   ├── gcs.py               # GCSProvider
│   ├── azure.py             # AzureBlobProvider
│   └── r2.py                # R2 convenience (thin wrapper around S3)
├── commands/
│   ├── __init__.py
│   ├── navigation.py        # ls, cd, tree, pwd
│   ├── read.py              # cat, peek, head, open
│   ├── transfer.py          # get, put, cp, mirror, diff
│   ├── modify.py            # dangerously-delete-content
│   ├── search.py            # find, grep
│   ├── recon.py             # enum (enumeration + triage), th (TruffleHog), scope
│   ├── findings.py          # tag, findings, export
│   ├── session.py           # session save/load/list/delete
│   ├── info.py              # stats, du, info, presign, crawlstatus
│   ├── audit.py             # audit subcommands
│   └── shell.py             # help, clear, history, set, exit
├── data/
│   └── enum_rules.json      # Classification rules for enum (extensions, filenames, dir patterns)
├── config.json.example      # Example config
└── tests/
    ├── test_providers.py
    ├── test_commands.py
    ├── test_cache.py
    └── test_path_resolution.py
```

---

## 13. Open Questions

1. **Should multi-provider mode exist in a single session?** (e.g., `cd s3://` then `cd gs://` to switch). Complex but powerful for comparing buckets across clouds.
2. **Should `enum` rules be user-extensible?** e.g., custom `~/.bucketboss/enum_rules.json` to add org-specific patterns (internal hostnames, proprietary config file names).
3. **Should `th` support other scanners?** e.g., `gitleaks`, `detect-secrets` — or keep it TruffleHog-only for simplicity?
4. **Auto-enum on connect?** When connecting via `--url`, should BucketBoss auto-run a lightweight `enum --depth 1` and print a quick summary? Useful but might be slow on huge buckets. Maybe opt-in via `--auto-enum`.
5. **Should `cat`/`peek` auto-highlight secrets?** e.g., regex-highlight things that look like API keys, tokens, connection strings when displaying file contents — lightweight alternative to `th` for quick visual triage.
6. **Session sharing format?** Should `.bbsession.json` files be portable enough to share with teammates (e.g., attach to a Jira ticket), or strictly local?

---

*This document is the starting point. Each phase should produce its own detailed spec before implementation begins.*
