import argparse
import sys
import threading
import time

import boto3
import botocore
import botocore.client
from botocore.exceptions import ClientError

from .app import BucketBossApp
from .providers.base import CloudProvider
from .providers.s3 import S3Provider, MultiBucketProvider


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


def background_cache_crawl(provider, cache, status_dict, max_depth):
    """Target function for background thread to crawl and cache."""
    status_dict["status"] = "loading"
    status_dict["depth"] = 0
    status_dict["cached_prefixes"] = 0
    try:
        print(f"[Background crawl started: Max Depth {max_depth}]", file=sys.stderr)
        crawl_prefix_recursive(provider, cache, status_dict, '', 1, max_depth)
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


def main():
    args = parse_args()
    try:
        s3_client = create_s3_client(args)
    except Exception as e:
        print(f"Error creating S3 client: {e}", file=sys.stderr)
        return

    # Multi-bucket mode if no bucket provided
    if not args.bucket:
        provider = MultiBucketProvider(s3_client)
        print("BucketBoss Multi-Bucket Shell. Type 'help' or 'exit'.")
        app = BucketBossApp(provider)
        app.run()
        return

    provider = None

    try:
        provider = S3Provider(args.bucket, s3_client)
        provider.head_bucket()
        print(f"Successfully connected to S3 bucket: {args.bucket}")

        app = BucketBossApp(provider)

        # Start background stats collection
        print("Initiating background stats collection...")
        stats_thread = threading.Thread(
            target=collect_stats_background,
            args=(provider, app.stats_result),
            daemon=True,
        )
        stats_thread.start()

        # Start background cache crawl
        crawl_depth = 2
        if crawl_depth > 0:
            print(f"Initiating background cache crawl (max depth {crawl_depth})...")
            crawl_thread = threading.Thread(
                target=background_cache_crawl,
                args=(provider, app.cache, app.crawl_status, crawl_depth),
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
