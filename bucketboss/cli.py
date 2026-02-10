import argparse
import sys
import threading
import time

import boto3
import botocore
import botocore.client
from botocore.exceptions import ClientError

from .app import BucketBossApp
from .config import load_config, get_workers
from .providers.base import CloudProvider
from .providers.s3 import S3Provider, MultiBucketProvider
from .providers.s3xml import S3XMLProvider, parse_s3_url


def create_s3_client(args):
    if args.profile:
        session = boto3.Session(profile_name=args.profile)
        return session.client('s3')
    elif args.access_key and args.secret_key:
        return boto3.client(
            's3',
            aws_access_key_id=args.access_key,
            aws_secret_access_key=args.secret_key,
        )
    else:
        return boto3.client(
            's3',
            config=botocore.client.Config(signature_version=botocore.UNSIGNED),
        )


def parse_args():
    parser = argparse.ArgumentParser(description='BucketBoss - Interactive Cloud Storage Shell')
    parser.add_argument('--bucket', required=False, help='S3 bucket name (optional; omit to list all buckets)')
    parser.add_argument('--url', help='S3 HTTP URL for SDK-free XML access (e.g. https://bucket.s3.us-west-2.amazonaws.com/)')
    parser.add_argument('--provider', choices=['s3', 's3xml'], default='s3', help='Provider backend (default: s3)')
    parser.add_argument('--config', dest='config_path', default=None, help='Path to config file (default: ~/.bucketboss/config.json)')
    group = parser.add_argument_group('S3 Authentication methods')
    group.add_argument('--profile', help='AWS CLI profile name for S3')
    group.add_argument('--access-key', help='AWS access key for S3')
    parser.add_argument('--secret-key', help='AWS secret key for S3')
    args = parser.parse_args()

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
        if isinstance(e, ClientError):
            result_dict["error_message"] = f"API Error: {e.response.get('Error', {}).get('Code', 'Unknown')}"
        else:
            result_dict["error_message"] = f"Unexpected error: {str(e)}"


# --- Background Cache Crawl ---
def crawl_prefix_recursive(provider, cache, status_dict, prefix, current_depth, max_depth):
    """Recursively list and cache directories up to max_depth."""
    if current_depth > max_depth:
        return

    from .app import CACHE_TTL_SECONDS

    status_dict["depth"] = max(status_dict.get("depth", 0), current_depth)

    entry = cache.get(prefix)
    if entry and time.time() - entry[2] < CACHE_TTL_SECONDS:
        dirs = entry[0]
    else:
        try:
            dirs, files, _ = provider.list_objects(prefix)
            cache[prefix] = (dirs, files, time.time())
            status_dict["cached_prefixes"] = status_dict.get("cached_prefixes", 0) + 1
        except Exception as e:
            print(f"[Crawl: Error listing prefix '{prefix or '<root>'}': {e}]", file=sys.stderr)
            return

    for subdir in dirs:
        if subdir:
            next_prefix = prefix + subdir + '/'
            crawl_prefix_recursive(provider, cache, status_dict, next_prefix, current_depth + 1, max_depth)


def background_cache_crawl(provider, cache, status_dict, max_depth, workers=16):
    """Target function for background thread to crawl and cache using parallel BFS."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .app import CACHE_TTL_SECONDS

    status_dict["status"] = "loading"
    status_dict["depth"] = 0
    status_dict["cached_prefixes"] = 0
    try:
        print(f"[Background crawl started: Max Depth {max_depth}, Workers {workers}]", file=sys.stderr)

        current_level = [('', 1)]  # (prefix, depth)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            while current_level:
                next_level = []
                future_to_prefix = {}

                for prefix, depth in current_level:
                    entry = cache.get(prefix)
                    if entry and time.time() - entry[2] < CACHE_TTL_SECONDS:
                        # Already cached — still need to queue subdirs
                        dirs = entry[0]
                        status_dict["depth"] = max(status_dict.get("depth", 0), depth)
                        if depth < max_depth:
                            for d in dirs:
                                if d:
                                    next_level.append((prefix + d + '/', depth + 1))
                    else:
                        future = executor.submit(provider.list_objects, prefix)
                        future_to_prefix[future] = (prefix, depth)

                for future in as_completed(future_to_prefix):
                    prefix, depth = future_to_prefix[future]
                    try:
                        dirs, files, _ = future.result()
                        cache[prefix] = (dirs, files, time.time())
                        status_dict["cached_prefixes"] = status_dict.get("cached_prefixes", 0) + 1
                        status_dict["depth"] = max(status_dict.get("depth", 0), depth)

                        if depth < max_depth:
                            for d in dirs:
                                if d:
                                    next_level.append((prefix + d + '/', depth + 1))
                    except Exception as e:
                        print(f"[Crawl: Error listing prefix '{prefix or '<root>'}': {e}]", file=sys.stderr)

                current_level = next_level

        status_dict["status"] = "complete"
        print(
            f"[Background crawl finished. Max Depth: {status_dict['depth']}, "
            f"Prefixes Cached: {status_dict['cached_prefixes']}]",
            file=sys.stderr,
        )
    except Exception as e:
        status_dict["status"] = "error"
        status_dict["error_message"] = f"Unexpected error during crawl: {str(e)}"
        print(f"[Background crawl failed: {e}]", file=sys.stderr)


def probe_permissions(provider):
    """Probe what permissions we have on the bucket."""
    perms = {"list": False, "read": False, "stats": False}

    # Probe list
    first_file_key = None
    try:
        dirs, files, _ = provider.list_objects('', limit=1)
        perms["list"] = True
        if files:
            first_file_key = files[0]['name']
        elif dirs:
            # Try listing inside the first directory to find a file
            try:
                _, sub_files, _ = provider.list_objects(dirs[0] + '/', limit=1)
                if sub_files:
                    first_file_key = dirs[0] + '/' + sub_files[0]['name']
            except Exception:
                pass
    except Exception:
        pass

    # Probe read
    if first_file_key:
        try:
            provider.get_object_metadata(first_file_key)
            perms["read"] = True
        except Exception:
            pass

    # Probe stats
    try:
        provider.get_bucket_stats()
        perms["stats"] = True
    except Exception:
        pass

    return perms


def _print_banner(provider, perms):
    """Print the startup banner."""
    bucket_name = getattr(provider, 'bucket_name', 'unknown')

    # Determine transport
    from .providers.s3xml import S3XMLProvider
    from .providers.s3 import S3Provider
    if isinstance(provider, S3XMLProvider):
        transport = "HTTP/XML (unsigned)"
    elif isinstance(provider, S3Provider):
        transport = "boto3"
    else:
        transport = "unknown"

    list_icon = '✅' if perms['list'] else '❌'
    read_icon = '✅' if perms['read'] else '❌'
    stats_icon = '✅' if perms['stats'] else '❌'

    print("")
    print("🪣 BucketBoss v0.1.0")
    print("   Target:    s3://%s/" % bucket_name)
    print("   Transport: %s" % transport)
    print("   Access:    %s List  %s Read  %s Stats" % (list_icon, read_icon, stats_icon))
    print("")


def main():
    args = parse_args()
    config = load_config(getattr(args, 'config_path', None))
    workers = get_workers(config)

    # Handle --url / --provider s3xml mode (no SDK required)
    if args.url or args.provider == 's3xml':
        if not args.url:
            print("Error: --url is required when using --provider s3xml", file=sys.stderr)
            return
        try:
            base_url, bucket_name = parse_s3_url(args.url)
            provider = S3XMLProvider(base_url, bucket_name)
            provider.head_bucket()

            perms = probe_permissions(provider)
            _print_banner(provider, perms)

            app = BucketBossApp(provider)
            app.config = config

            stats_thread = threading.Thread(
                target=collect_stats_background,
                args=(provider, app.stats_result),
                daemon=True,
            )
            stats_thread.start()

            crawl_depth = config.get("general", {}).get("crawl_depth", 2)
            if crawl_depth > 0:
                crawl_thread = threading.Thread(
                    target=background_cache_crawl,
                    args=(provider, app.cache, app.crawl_status, crawl_depth, workers),
                    daemon=True,
                )
                crawl_thread.start()

            app.run()
        except (PermissionError, FileNotFoundError, ConnectionError) as e:
            print(f"Error: {e}", file=sys.stderr)
        except ValueError as e:
            print(f"Error parsing URL: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error connecting via HTTP: {e}", file=sys.stderr)
        return

    try:
        s3_client = create_s3_client(args)
    except Exception as e:
        print(f"Error creating S3 client: {e}", file=sys.stderr)
        return

    # Multi-bucket mode if no bucket provided
    if not args.bucket:
        provider = MultiBucketProvider(s3_client)
        try:
            provider.head_bucket()
        except Exception:
            print("Error: Cannot list buckets. Multi-bucket mode requires AWS credentials.")
            print("")
            print("  Options:")
            print("    bb --profile <profile>              # use an AWS CLI profile")
            print("    bb --access-key <key> --secret-key <secret>")
            print("")
            print("  For open/public buckets, specify the bucket directly:")
            print("    bb --bucket <name>                  # via boto3 (unsigned)")
            print("    bb --url https://bucket.s3.amazonaws.com/  # via HTTP/XML")
            return
        print("BucketBoss Multi-Bucket Shell. Type 'help' or 'exit'.")
        app = BucketBossApp(provider)
        app.config = config
        app.run()
        return

    provider = None

    try:
        provider = S3Provider(args.bucket, s3_client)
        provider.head_bucket()

        perms = probe_permissions(provider)
        _print_banner(provider, perms)

        app = BucketBossApp(provider)
        app.config = config

        stats_thread = threading.Thread(
            target=collect_stats_background,
            args=(provider, app.stats_result),
            daemon=True,
        )
        stats_thread.start()

        crawl_depth = config.get("general", {}).get("crawl_depth", 2)
        if crawl_depth > 0:
            crawl_thread = threading.Thread(
                target=background_cache_crawl,
                args=(provider, app.cache, app.crawl_status, crawl_depth, workers),
                daemon=True,
            )
            crawl_thread.start()

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

    if not provider:
        print("Error: Could not initialize cloud provider.")
        return

    app.run()
