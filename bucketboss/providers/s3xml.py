import os
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple, List
from urllib.parse import urlparse, quote, urlencode
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from .base import CloudProvider

S3_NS = 'http://s3.amazonaws.com/doc/2006-03-01/'
DEFAULT_TIMEOUT = 30


def parse_s3_url(url: str) -> Tuple[str, str]:
    """Parse an S3 URL into (base_url, bucket_name).

    Supported formats:
      - https://BUCKET.s3.amazonaws.com/          (virtual-hosted)
      - https://BUCKET.s3.REGION.amazonaws.com/   (virtual-hosted with region)
      - https://s3.amazonaws.com/BUCKET/           (path style)
      - https://s3.REGION.amazonaws.com/BUCKET/    (path style with region)
      - https://custom-host:9000/BUCKET/           (S3-compatible endpoint)
    """
    parsed = urlparse(url)
    host = parsed.hostname or ''
    path_parts = [p for p in parsed.path.split('/') if p]

    # Virtual-hosted style: BUCKET.s3.amazonaws.com or BUCKET.s3.REGION.amazonaws.com
    if host.endswith('.amazonaws.com'):
        labels = host.split('.')
        scheme = parsed.scheme or 'https'

        # Check for virtual-hosted with region: BUCKET.s3.REGION.amazonaws.com
        # labels = ['mybucket', 's3', 'us-west-2', 'amazonaws', 'com']
        if len(labels) == 5 and labels[1] == 's3':
            bucket_name = labels[0]
            base_url = f"{scheme}://{host}"
            return base_url, bucket_name

        # Check for virtual-hosted: BUCKET.s3.amazonaws.com
        # labels = ['mybucket', 's3', 'amazonaws', 'com']
        if len(labels) == 4 and labels[1] == 's3':
            bucket_name = labels[0]
            base_url = f"{scheme}://{host}"
            return base_url, bucket_name

        # Path style with region: s3.REGION.amazonaws.com/BUCKET
        # labels = ['s3', 'us-west-2', 'amazonaws', 'com']
        if len(labels) == 4 and labels[0] == 's3':
            if not path_parts:
                raise ValueError(f"Cannot determine bucket from URL: {url}")
            bucket_name = path_parts[0]
            base_url = f"{scheme}://{host}/{bucket_name}"
            return base_url, bucket_name

        # Path style: s3.amazonaws.com/BUCKET
        # labels = ['s3', 'amazonaws', 'com']
        if len(labels) == 3 and labels[0] == 's3':
            if not path_parts:
                raise ValueError(f"Cannot determine bucket from URL: {url}")
            bucket_name = path_parts[0]
            base_url = f"{scheme}://{host}/{bucket_name}"
            return base_url, bucket_name

    # Custom S3-compatible endpoint: https://host:port/BUCKET/
    if not path_parts:
        raise ValueError(f"Cannot determine bucket from URL: {url}")
    bucket_name = path_parts[0]
    scheme = parsed.scheme or 'https'
    port_str = f":{parsed.port}" if parsed.port else ''
    base_url = f"{scheme}://{host}{port_str}/{bucket_name}"
    return base_url, bucket_name


class S3XMLProvider(CloudProvider):
    """S3 provider using raw HTTP/XML — no SDK dependencies."""

    def __init__(self, base_url: str, bucket_name: str):
        self.base_url = base_url.rstrip('/')
        self.bucket_name = bucket_name
        self._use_list_type_2 = True

    def get_prompt_prefix(self) -> str:
        return f"s3://{self.bucket_name}/"

    def head_bucket(self):
        url = f"{self.base_url}?max-keys=0"
        try:
            req = urllib.request.Request(url, method='GET')
            urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise PermissionError(f"Access denied to bucket '{self.bucket_name}'")
            elif e.code == 404:
                raise FileNotFoundError(f"Bucket '{self.bucket_name}' not found")
            else:
                raise ConnectionError(f"HTTP {e.code}: {e.reason}")

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
            if self._use_list_type_2:
                dirs, files, next_continuation_token = self._list_objects_v2(
                    prefix, limit, next_token
                )
            else:
                dirs, files, next_continuation_token = self._list_objects_v1(
                    prefix, limit, next_token
                )
            directories = dirs
        except urllib.error.HTTPError as e:
            if self._use_list_type_2 and e.code in (400, 501):
                # Fall back to list-type=1
                self._use_list_type_2 = False
                directories, files, next_continuation_token = self._list_objects_v1(
                    prefix, limit, next_token
                )
            else:
                self._handle_http_error(e, f"listing objects at '{prefix}'")
                return [], [], None
        except urllib.error.URLError as e:
            print(f"Error listing objects at '{prefix}': {e.reason}", file=sys.stderr)
            return [], [], None

        directories.sort()
        if sort_key == 'name':
            files.sort(key=lambda x: x['name'])
        elif sort_key == 'date':
            files.sort(key=lambda x: x['last_modified'], reverse=True)
        elif sort_key == 'size':
            files.sort(key=lambda x: x['size'], reverse=True)

        # If no limit, paginate through everything
        if limit is None and next_continuation_token:
            while next_continuation_token:
                try:
                    if self._use_list_type_2:
                        more_dirs, more_files, next_continuation_token = self._list_objects_v2(
                            prefix, None, next_continuation_token
                        )
                    else:
                        more_dirs, more_files, next_continuation_token = self._list_objects_v1(
                            prefix, None, next_continuation_token
                        )
                    directories.extend(more_dirs)
                    files.extend(more_files)
                except Exception:
                    break

            directories.sort()
            if sort_key == 'name':
                files.sort(key=lambda x: x['name'])
            elif sort_key == 'date':
                files.sort(key=lambda x: x['last_modified'], reverse=True)
            elif sort_key == 'size':
                files.sort(key=lambda x: x['size'], reverse=True)
            next_continuation_token = None

        return directories, files, next_continuation_token

    def _list_objects_v2(
        self, prefix: str, limit: Optional[int], continuation_token: Optional[str]
    ) -> Tuple[List[str], List[dict], Optional[str]]:
        params = {
            'list-type': '2',
            'delimiter': '/',
            'prefix': prefix,
        }
        if limit is not None:
            params['max-keys'] = str(limit)
        if continuation_token:
            params['continuation-token'] = continuation_token

        url = f"{self.base_url}?{urlencode(params)}"
        req = urllib.request.Request(url, method='GET')
        resp = urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
        body = resp.read()
        return self._parse_list_response(body, prefix, v2=True)

    def _list_objects_v1(
        self, prefix: str, limit: Optional[int], marker: Optional[str]
    ) -> Tuple[List[str], List[dict], Optional[str]]:
        params = {
            'delimiter': '/',
            'prefix': prefix,
        }
        if limit is not None:
            params['max-keys'] = str(limit)
        if marker:
            params['marker'] = marker

        url = f"{self.base_url}?{urlencode(params)}"
        req = urllib.request.Request(url, method='GET')
        resp = urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
        body = resp.read()
        return self._parse_list_response(body, prefix, v2=False)

    def _parse_list_response(
        self, body: bytes, prefix: str, v2: bool
    ) -> Tuple[List[str], List[dict], Optional[str]]:
        directories = []
        files = []
        next_token = None

        root = ET.fromstring(body)

        # Handle both namespaced and non-namespaced XML
        ns = ''
        if root.tag.startswith('{'):
            ns = root.tag.split('}')[0] + '}'

        for cp in root.findall(f'{ns}CommonPrefixes'):
            prefix_elem = cp.find(f'{ns}Prefix')
            if prefix_elem is not None and prefix_elem.text:
                dir_path = prefix_elem.text
                dir_name = dir_path[len(prefix):].rstrip('/')
                if dir_name:
                    directories.append(dir_name)

        for contents in root.findall(f'{ns}Contents'):
            key_elem = contents.find(f'{ns}Key')
            if key_elem is None or not key_elem.text:
                continue
            file_key = key_elem.text
            if file_key == prefix:
                continue
            file_name = file_key[len(prefix):]
            if not file_name:
                continue

            size_elem = contents.find(f'{ns}Size')
            size = int(size_elem.text) if size_elem is not None and size_elem.text else 0

            last_modified_elem = contents.find(f'{ns}LastModified')
            last_modified = self._parse_datetime(
                last_modified_elem.text if last_modified_elem is not None else None
            )

            files.append({
                'name': file_name,
                'size': size,
                'last_modified': last_modified,
                'extension': os.path.splitext(file_name)[1].lower(),
            })

        # Pagination token
        is_truncated_elem = root.find(f'{ns}IsTruncated')
        is_truncated = (
            is_truncated_elem is not None
            and is_truncated_elem.text
            and is_truncated_elem.text.lower() == 'true'
        )

        if is_truncated:
            if v2:
                token_elem = root.find(f'{ns}NextContinuationToken')
                if token_elem is not None and token_elem.text:
                    next_token = token_elem.text
            else:
                # v1 uses marker-based pagination; use the last key as next marker
                if files:
                    last_key = prefix + files[-1]['name']
                    next_token = last_key
                elif directories:
                    last_key = prefix + directories[-1] + '/'
                    next_token = last_key

        return directories, files, next_token

    def _parse_datetime(self, dt_str: Optional[str]) -> datetime:
        if not dt_str:
            return datetime.now(timezone.utc)
        try:
            # Handle ISO 8601 with Z suffix
            cleaned = dt_str.replace('Z', '+00:00')
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return datetime.now(timezone.utc)

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
        url = f"{self.base_url}/{quote(key, safe='/')}"
        try:
            req = urllib.request.Request(url, method='GET')
            resp = urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
            return resp.read()
        except urllib.error.HTTPError as e:
            self._handle_http_error(e, f"getting object '{key}'")
            raise
        except urllib.error.URLError as e:
            print(f"Error getting object '{key}': {e.reason}", file=sys.stderr)
            raise

    def download_file(self, key: str, local_path: str):
        url = f"{self.base_url}/{quote(key, safe='/')}"
        try:
            req = urllib.request.Request(url, method='GET')
            resp = urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
            with open(local_path, 'wb') as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
        except urllib.error.HTTPError as e:
            self._handle_http_error(e, f"downloading '{key}'")
            raise
        except urllib.error.URLError as e:
            print(f"Error downloading '{key}': {e.reason}", file=sys.stderr)
            raise

    def upload_file(self, local_path: str, key: str):
        raise NotImplementedError("S3 XML provider is read-only")

    def read_object_range(self, key: str, size: int) -> bytes:
        if size <= 0:
            raise ValueError(f"Size must be positive, got: {size}")
        url = f"{self.base_url}/{quote(key, safe='/')}"
        try:
            req = urllib.request.Request(url, method='GET')
            req.add_header('Range', f'bytes=0-{size - 1}')
            resp = urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
            return resp.read()
        except urllib.error.HTTPError as e:
            self._handle_http_error(e, f"reading range of '{key}'")
            raise
        except urllib.error.URLError as e:
            print(f"Error reading range of '{key}': {e.reason}", file=sys.stderr)
            raise

    def get_object_metadata(self, key: str) -> dict:
        url = f"{self.base_url}/{quote(key, safe='/')}"
        try:
            req = urllib.request.Request(url, method='HEAD')
            resp = urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
            headers = resp.headers

            size = int(headers.get('Content-Length', 0))
            content_type = headers.get('Content-Type', 'application/octet-stream')
            last_modified_str = headers.get('Last-Modified')

            if last_modified_str:
                try:
                    from email.utils import parsedate_to_datetime
                    last_modified = parsedate_to_datetime(last_modified_str)
                except Exception:
                    last_modified = datetime.now(timezone.utc)
            else:
                last_modified = datetime.now(timezone.utc)

            return {
                'size': size,
                'last_modified': last_modified,
                'content_type': content_type,
            }
        except urllib.error.HTTPError as e:
            self._handle_http_error(e, f"getting metadata for '{key}'")
            raise
        except urllib.error.URLError as e:
            print(f"Error getting metadata for '{key}': {e.reason}", file=sys.stderr)
            raise

    def get_bucket_stats(self) -> dict:
        return {
            "Location": "unknown (HTTP mode)",
            "Size": "N/A",
        }

    def _handle_http_error(self, e: urllib.error.HTTPError, context: str):
        if e.code == 403:
            print(f"Access denied ({context})", file=sys.stderr)
        elif e.code == 404:
            print(f"Not found ({context})", file=sys.stderr)
        else:
            print(f"HTTP {e.code}: {e.reason} ({context})", file=sys.stderr)
