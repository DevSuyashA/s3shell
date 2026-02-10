import os
import sys
from typing import Optional, Tuple, List

from botocore.exceptions import ClientError

from .base import CloudProvider


class S3Provider(CloudProvider):
    def __init__(self, bucket_name: str, s3_client):
        self.bucket_name = bucket_name
        self.s3_client = s3_client

    def get_prompt_prefix(self) -> str:
        return f"s3://{self.bucket_name}/"

    def head_bucket(self):
        self.s3_client.head_bucket(Bucket=self.bucket_name)

    def list_objects(
        self,
        prefix: str,
        sort_key: str = 'name',
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
    ) -> Tuple[List[str], List[dict], Optional[str]]:
        directories = []
        files = []
        next_continuation_token = None

        try:
            if limit is not None:
                kwargs = {
                    'Bucket': self.bucket_name,
                    'Prefix': prefix,
                    'Delimiter': '/',
                }
                kwargs['MaxKeys'] = limit
                if next_token:
                    kwargs['ContinuationToken'] = next_token

                response = self.s3_client.list_objects_v2(**kwargs)

                for cp in response.get('CommonPrefixes', []):
                    dir_path = cp['Prefix']
                    dir_name = dir_path[len(prefix):].rstrip('/')
                    if dir_name:
                        directories.append(dir_name)

                for obj in response.get('Contents', []):
                    file_key = obj['Key']
                    if file_key == prefix:
                        continue
                    file_name = file_key[len(prefix):]
                    if file_name:
                        files.append({
                            'name': file_name,
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'],
                            'extension': os.path.splitext(file_name)[1].lower(),
                        })

                next_continuation_token = response.get('NextContinuationToken')

            else:
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
                                'extension': os.path.splitext(file_name)[1].lower(),
                            })
                next_continuation_token = None

            directories.sort()
            if sort_key == 'name':
                files.sort(key=lambda x: x['name'])
            elif sort_key == 'date':
                files.sort(key=lambda x: x['last_modified'], reverse=True)
            elif sort_key == 'size':
                files.sort(key=lambda x: x['size'], reverse=True)

            return directories, files, next_continuation_token

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            print(f"Error listing S3 objects at '{prefix}': {error_code}", file=sys.stderr)
            raise
        except Exception as e:
            print(f"Error listing S3 objects: {str(e)}", file=sys.stderr)
            raise

    def resolve_path(self, current_prefix: str, input_path: str, is_directory: bool = False) -> str:
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
        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
        return response['Body'].read()

    def download_file(self, key: str, local_path: str):
        self.s3_client.download_file(self.bucket_name, key, local_path)

    def upload_file(self, local_path: str, key: str):
        self.s3_client.upload_file(local_path, self.bucket_name, key)

    def read_object_range(self, key: str, size: int) -> bytes:
        if size <= 0:
            raise ValueError(f"Size must be positive, got: {size}")
        response = self.s3_client.get_object(
            Bucket=self.bucket_name, Key=key, Range=f'bytes=0-{size-1}'
        )
        return response['Body'].read()

    def get_object_metadata(self, key: str) -> dict:
        response = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
        return {
            'size': response['ContentLength'],
            'last_modified': response['LastModified'],
            'content_type': response.get('ContentType', 'application/octet-stream'),
        }

    def get_bucket_stats(self) -> dict:
        """Get S3 bucket location and creation date."""
        stats = {}
        try:
            # --- Get Location ---
            try:
                location_response = self.s3_client.get_bucket_location(Bucket=self.bucket_name)
                if location_response:
                    stats['Location'] = location_response.get('LocationConstraint') or 'us-east-1'
                else:
                    stats['Location'] = "Error: Received no response for location."
            except ClientError as e_loc:
                stats['Location'] = f"Error: {e_loc.response.get('Error', {}).get('Code', 'Unknown')}"
            except Exception as e_loc_other:
                stats['Location'] = f"Unexpected error getting location: {str(e_loc_other)}"

            # --- Get Creation Date ---
            try:
                list_response = self.s3_client.list_buckets()
                found_date = False
                if list_response and 'Buckets' in list_response:
                    for bucket in list_response.get('Buckets') or []:
                        if bucket and bucket.get('Name') == self.bucket_name and bucket.get('CreationDate'):
                            stats['CreationDate'] = bucket['CreationDate'].isoformat()
                            found_date = True
                            break
                if not found_date:
                    if 'CreationDate' not in stats:
                        stats['CreationDate'] = "Not found (or requires list_buckets permission)"
            except ClientError as e_date:
                if 'CreationDate' not in stats:
                    stats['CreationDate'] = f"Error: {e_date.response.get('Error', {}).get('Code', 'Unknown')}"
            except Exception:
                if 'CreationDate' not in stats:
                    stats['CreationDate'] = "Unexpected error processing creation date data."

            # --- Size Placeholder ---
            stats['Size'] = "N/A (Requires separate calculation)"

        except Exception as outer_e:
            print(f"General error during get_bucket_stats: {str(outer_e)}", file=sys.stderr)
            if 'Location' not in stats:
                stats['Location'] = "Error retrieving"
            if 'CreationDate' not in stats:
                stats['CreationDate'] = "Error retrieving"
            if 'Size' not in stats:
                stats['Size'] = "N/A"

        return stats


class MultiBucketProvider(CloudProvider):
    """Provider that lists all buckets at root, then delegates to single-bucket provider."""

    def __init__(self, s3_client):
        self.s3_client = s3_client

    def get_prompt_prefix(self) -> str:
        return "s3://"

    def head_bucket(self):
        self.s3_client.list_buckets()

    def list_objects(
        self,
        prefix: str,
        sort_key: str = 'name',
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
    ) -> Tuple[List[str], List[dict], Optional[str]]:
        if prefix == '':
            try:
                resp = self.s3_client.list_buckets()
                buckets = [b['Name'] for b in resp.get('Buckets', [])]
                buckets.sort()
                return buckets, [], None
            except Exception as e:
                print(f"Error listing buckets: {e}", file=sys.stderr)
                return [], [], None
        bucket_name, _, sub_prefix = prefix.partition('/')
        s3p = S3Provider(bucket_name, self.s3_client)
        return s3p.list_objects(sub_prefix, sort_key, limit, next_token)

    def resolve_path(self, current_prefix: str, input_path: str, is_directory: bool = False) -> str:
        if input_path.startswith('/'):
            path = input_path.lstrip('/')
        else:
            path = (current_prefix or '') + input_path
        parts = path.split('/')
        normalized = []
        for part in parts:
            if part == '..':
                if normalized:
                    normalized.pop()
            elif part and part != '.':
                normalized.append(part)
        new_path = '/'.join(normalized)
        if is_directory and new_path:
            new_path += '/'
        elif not is_directory and new_path.endswith('/'):
            new_path = new_path.rstrip('/')
        return new_path

    def get_object(self, key: str) -> bytes:
        key = key.lstrip('/')
        bucket_name, _, subkey = key.partition('/')
        if not bucket_name:
            raise ValueError(f"Invalid S3 key, missing bucket name: '{key}'")
        s3p = S3Provider(bucket_name, self.s3_client)
        return s3p.get_object(subkey)

    def download_file(self, key: str, local_path: str):
        key = key.lstrip('/')
        bucket_name, _, subkey = key.partition('/')
        if not bucket_name:
            raise ValueError(f"Invalid S3 key, missing bucket name: '{key}'")
        s3p = S3Provider(bucket_name, self.s3_client)
        s3p.download_file(subkey, local_path)

    def upload_file(self, local_path: str, key: str):
        key = key.lstrip('/')
        bucket_name, _, subkey = key.partition('/')
        if not bucket_name:
            raise ValueError(f"Invalid S3 key, missing bucket name: '{key}'")
        s3p = S3Provider(bucket_name, self.s3_client)
        s3p.upload_file(local_path, subkey)

    def read_object_range(self, key: str, size: int) -> bytes:
        key = key.lstrip('/')
        bucket_name, _, subkey = key.partition('/')
        if not bucket_name:
            raise ValueError(f"Invalid S3 key, missing bucket name: '{key}'")
        s3p = S3Provider(bucket_name, self.s3_client)
        return s3p.read_object_range(subkey, size)

    def get_object_metadata(self, key: str) -> dict:
        key = key.lstrip('/')
        bucket_name, _, subkey = key.partition('/')
        if not bucket_name:
            raise ValueError(f"Invalid S3 key, missing bucket name: '{key}'")
        s3p = S3Provider(bucket_name, self.s3_client)
        return s3p.get_object_metadata(subkey)

    def get_bucket_stats(self) -> dict:
        stats = {}
        try:
            resp = self.s3_client.list_buckets()
            stats['BucketCount'] = len(resp.get('Buckets', []))
        except Exception as e:
            stats['BucketCount'] = f"Error: {e}"
        return stats
