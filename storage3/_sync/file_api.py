from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from io import BufferedReader, FileIO
from pathlib import Path
from typing import Any, Optional, Union, cast

from httpx import HTTPError, Response

from ..constants import DEFAULT_FILE_OPTIONS, DEFAULT_SEARCH_OPTIONS
from ..types import (
    BaseBucket,
    CreateSignedURLOptions,
    CreateSignedURLsOptions,
    FileOptions,
    ListBucketFilesOptions,
    RequestMethod,
    SignedUploadURL,
    TransformOptions,
)
from ..utils import StorageException
from supabase_client import SupaSyncClient

__all__ = ["SyncBucket"]


class SyncBucketActionsMixin:
    """Functions needed to access the file API."""

    id: str
    _client: SupaSyncClient

    def _request(
        self,
        method: RequestMethod,
        url: str,
        headers: Optional[dict[str, Any]] = None,
        json: Optional[dict[Any, Any]] = None,
        files: Optional[Any] = None,
    ) -> Response:
        response = self._client.request(
            method,
            url,
            headers=headers or {},
            json=json,
            files=files,
        )
        try:
            response.raise_for_status()
        except HTTPError:
            raise StorageException(
                {**response.json(), "statusCode": response.status_code}
            )

        return response

    def create_signed_upload_url(self, path: str) -> SignedUploadURL:
        """
        Creates a signed upload URL.

        Parameters
        ----------
        path
            The file path, including the file name. For example `folder/image.png`.
        """
        _path = self._get_final_path(path)
        response = self._request("POST", f"/object/upload/sign/{_path}")
        data = response.json()
        full_url: urllib.parse.ParseResult = urllib.parse.urlparse(
            str(self._client.base_url) + data["url"]
        )
        query_params = urllib.parse.parse_qs(full_url.query)
        if not query_params.get("token"):
            raise StorageException("No token sent by the API")
        return {
            "signed_url": full_url.geturl(),
            "token": query_params["token"][0],
            "path": path,
        }

    def upload_to_signed_url(
        self,
        path: str,
        token: str,
        file: Union[BufferedReader, bytes, FileIO, str, Path],
        file_options: Optional[FileOptions] = None,
    ) -> Response:
        """
        Upload a file with a token generated from :meth:`.create_signed_url`

        Parameters
        ----------
        path
            The file path, including the file name
        token
            The token generated from :meth:`.create_signed_url`
        file
            The file contents or a file-like object to upload
        file_options
            Additional options for the uploaded file
        """
        _path = self._get_final_path(path)
        _url = urllib.parse.urlparse(f"/object/upload/sign/{_path}")
        query_params = urllib.parse.urlencode({"token": token})
        final_url = f"{_url.geturl()}?{query_params}"

        if file_options is None:
            file_options = {}

        cache_control = file_options.get("cache-control")
        if cache_control:
            file_options["cache-control"] = f"max-age={cache_control}"

        headers = {
            **self._client.headers,
            **DEFAULT_FILE_OPTIONS,
            **file_options,
        }
        filename = path.rsplit("/", maxsplit=1)[-1]

        if (
            isinstance(file, BufferedReader)
            or isinstance(file, bytes)
            or isinstance(file, FileIO)
        ):
            # bytes or byte-stream-like object received
            _file = {"file": (filename, file, headers.pop("content-type"))}
        else:
            # str or pathlib.path received
            _file = {
                "file": (
                    filename,
                    open(file, "rb"),
                    headers.pop("content-type"),
                )
            }
        return self._request(
            "PUT",
            final_url,
            files=_file,
            headers=headers,
        )

    def create_signed_url(
        self, path: str, expires_in: int, options: CreateSignedURLOptions = {}
    ) -> dict[str, str]:
        """
        Parameters
        ----------
        path
            file path to be downloaded, including the current file name.
        expires_in
            number of seconds until the signed URL expires.
        options
            options to be passed for downloading or transforming the file.
        """
        json = {"expiresIn": str(expires_in)}
        if options.get("download"):
            json.update({"download": options["download"]})
        if options.get("transform"):
            json.update({"transform": options["transform"]})

        path = self._get_final_path(path)
        response = self._request(
            "POST",
            f"/object/sign/{path}",
            json=json,
        )
        data = response.json()
        data[
            "signedURL"
        ] = f"{self._client.base_url}{cast(str, data['signedURL']).lstrip('/')}"
        return data

    def create_signed_urls(
        self, paths: list[str], expires_in: int, options: CreateSignedURLsOptions = {}
    ) -> list[dict[str, str]]:
        """
        Parameters
        ----------
        path
            file path to be downloaded, including the current file name.
        expires_in
            number of seconds until the signed URL expires.
        options
            options to be passed for downloading the file.
        """
        json = {"paths": paths, "expiresIn": str(expires_in)}
        if options.get("download"):
            json.update({"download": options["download"]})

        response = self._request(
            "POST",
            f"/object/sign/{self.id}",
            json=json,
        )
        data = response.json()
        for item in data:
            item[
                "signedURL"
            ] = f"{self._client.base_url}{cast(str, item['signedURL']).lstrip('/')}"
        return data

    def get_public_url(self, path: str, options: TransformOptions = {}) -> str:
        """
        Parameters
        ----------
        path
            file path, including the path and file name. For example `folder/image.png`.
        """
        render_path = "render/image" if options.get("transform") else "object"
        transformation_query = urllib.parse.urlencode(options)
        query_string = f"?{transformation_query}" if transformation_query else ""
        _path = self._get_final_path(path)
        return f"{self._client.base_url}{render_path}/public/{_path}{query_string}"

    def move(self, from_path: str, to_path: str) -> dict[str, str]:
        """
        Moves an existing file, optionally renaming it at the same time.

        Parameters
        ----------
        from_path
            The original file path, including the current file name. For example `folder/image.png`.
        to_path
            The new file path, including the new file name. For example `folder/image-copy.png`.
        """
        res = self._request(
            "POST",
            "/object/move",
            json={
                "bucketId": self.id,
                "sourceKey": from_path,
                "destinationKey": to_path,
            },
        )
        return res.json()

    def copy(self, from_path: str, to_path: str) -> dict[str, str]:
        """
        Copies an existing file to a new path in the same bucket.

        Parameters
        ----------
        from_path
            The original file path, including the current file name. For example `folder/image.png`.
        to_path
            The new file path, including the new file name. For example `folder/image-copy.png`.
        """
        res = self._request(
            "POST",
            "/object/copy",
            json={
                "bucketId": self.id,
                "sourceKey": from_path,
                "destinationKey": to_path,
            },
        )
        return res.json()

    def remove(self, paths: list) -> dict[str, str]:
        """
        Deletes files within the same bucket

        Parameters
        ----------
        paths
            An array or list of files to be deletes, including the path and file name. For example [`folder/image.png`].
        """
        response = self._request(
            "DELETE",
            f"/object/{self.id}",
            json={"prefixes": paths},
        )
        return response.json()

    def list(
        self,
        path: Optional[str] = None,
        options: Optional[ListBucketFilesOptions] = None,
    ) -> list[dict[str, str]]:
        """
        Lists all the files within a bucket.

        Parameters
        ----------
        path
            The folder path.
        options
            Search options, including `limit`, `offset`, and `sortBy`.
        """
        extra_options = options or {}
        extra_headers = {"Content-Type": "application/json"}
        body = {
            **DEFAULT_SEARCH_OPTIONS,
            **extra_options,
            "prefix": path or "",
        }
        response = self._request(
            "POST",
            f"/object/list/{self.id}",
            json=body,
            headers=extra_headers,
        )
        return response.json()

    def download(self, path: str, options: TransformOptions = {}) -> bytes:
        """
        Downloads a file.

        Parameters
        ----------
        path
            The file path to be downloaded, including the path and file name. For example `folder/image.png`.
        """
        render_path = (
            "render/image/authenticated" if options.get("transform") else "object"
        )
        transformation_query = urllib.parse.urlencode(options)
        query_string = f"?{transformation_query}" if transformation_query else ""

        _path = self._get_final_path(path)
        response = self._request(
            "GET",
            f"{render_path}/{_path}{query_string}",
        )
        return response.content

    def upload(
        self,
        path: str,
        file: Union[BufferedReader, bytes, FileIO, str, Path],
        file_options: Optional[FileOptions] = None,
    ) -> Response:
        """
        Uploads a file to an existing bucket.

        Parameters
        ----------
        path
            The relative file path including the bucket ID. Should be of the format `bucket/folder/subfolder/filename.png`.
            The bucket must already exist before attempting to upload.
        file
            The File object to be stored in the bucket. or a async generator of chunks
        file_options
            HTTP headers.
        """
        if file_options is None:
            file_options = {}
        cache_control = file_options.get("cache-control")
        if cache_control:
            file_options["cache-control"] = f"max-age={cache_control}"

        headers = {
            **self._client.headers,
            **DEFAULT_FILE_OPTIONS,
            **file_options,
        }
        filename = path.rsplit("/", maxsplit=1)[-1]

        if (
            isinstance(file, BufferedReader)
            or isinstance(file, bytes)
            or isinstance(file, FileIO)
        ):
            # bytes or byte-stream-like object received
            files = {"file": (filename, file, headers.pop("content-type"))}
        else:
            # str or pathlib.path received
            files = {
                "file": (
                    filename,
                    open(file, "rb"),
                    headers.pop("content-type"),
                )
            }

        _path = self._get_final_path(path)

        return self._request(
            "POST",
            f"/object/{_path}",
            files=files,
            headers=headers,
        )

    def _get_final_path(self, path: str) -> str:
        return f"{self.id}/{path}"


# this class is returned by methods that fetch buckets, for example StorageBucketAPI.get_bucket
# adding this mixin on the BaseBucket means that those bucket objects can also be used to
# run methods like `upload` and `download`
@dataclass(repr=False)
class SyncBucket(BaseBucket, SyncBucketActionsMixin):
    """Represents a storage bucket."""

    _client: SupaSyncClient = field(repr=False)


@dataclass
class SyncBucketProxy(SyncBucketActionsMixin):
    """A bucket proxy, this contains the minimum required fields to query the File API."""

    id: str
    _client: SupaSyncClient = field(repr=False)
