# SPDX-FileCopyrightText: 2015 Eric Larson, 2023 Frost Ming
#
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import os
import typing as t
from textwrap import dedent

from cacheyou.cache import BaseCache, SeparateBodyBaseCache
from cacheyou.controller import CacheController

if t.TYPE_CHECKING:
    from datetime import datetime

    from _typeshed import StrPath
    from filelock import BaseFileLock


def _secure_open_write(filename: StrPath, fmode: int) -> t.IO[bytes]:
    # We only want to write to this file, so open it in write only mode
    flags = os.O_WRONLY

    # os.O_CREAT | os.O_EXCL will fail if the file already exists, so we only
    #  will open *new* files.
    # We specify this because we want to ensure that the mode we pass is the
    # mode of the file.
    flags |= os.O_CREAT | os.O_EXCL

    # Do not follow symlinks to prevent someone from making a symlink that
    # we follow and insecurely open a cache file.
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    # On Windows we'll mark this file as binary
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    # Before we open our file, we want to delete any existing file that is
    # there
    try:
        os.remove(filename)
    except OSError:
        # The file must not exist already, so we can just skip ahead to opening
        pass

    # Open our file, the use of os.O_CREAT | os.O_EXCL will ensure that if a
    # race condition happens between the os.remove and this line, that an
    # error will be raised. Because we utilize a lockfile this should only
    # happen if someone is attempting to attack us.
    fd = os.open(filename, flags, fmode)
    try:
        return os.fdopen(fd, "wb")

    except:
        # An error occurred wrapping our FD in a file object
        os.close(fd)
        raise


class _FileCacheMixin:
    """Shared implementation for both FileCache variants."""

    def __init__(
        self,
        directory: StrPath,
        forever: bool = False,
        filemode: int = 0o0600,
        dirmode: int = 0o0700,
        lock_class: type[BaseFileLock] | None = None,
    ):
        try:
            if lock_class is None:
                from filelock import FileLock

                lock_class = FileLock
        except ImportError:
            notice = dedent(
                """
            NOTE: In order to use the FileCache you must have
            filelock installed. You can install it via pip:
              pip install filelock
            """
            )
            raise ImportError(notice) from None

        self.directory = directory
        self.forever = forever
        self.filemode = filemode
        self.dirmode = dirmode
        self.lock_class = lock_class

    @staticmethod
    def encode(x: str) -> str:
        return hashlib.sha224(x.encode()).hexdigest()

    def _fn(self, name: str) -> str:
        # NOTE: This method should not change as some may depend on it.
        #       See: https://github.com/ionrock/cachecontrol/issues/63
        hashed = self.encode(name)
        parts = [*list(hashed[:5]), hashed]
        return os.path.join(self.directory, *parts)

    def get(self, key: str) -> bytes | None:
        name = self._fn(key)
        try:
            with open(name, "rb") as fh:
                return fh.read()

        except FileNotFoundError:
            return None

    def set(self, key: str, value: bytes, expires: int | datetime | None = None):
        name = self._fn(key)
        self._write(name, value)

    def _write(self, path: str, data: bytes) -> None:
        """
        Safely write the data to the given path.
        """
        # Make sure the directory exists
        try:
            os.makedirs(os.path.dirname(path), self.dirmode)
        except OSError:
            pass

        with self.lock_class(path + ".lock"):
            # Write our actual file
            with _secure_open_write(path, self.filemode) as fh:
                fh.write(data)

    def _delete(self, key: str, suffix: str) -> None:
        name = self._fn(key) + suffix
        if not self.forever:
            try:
                os.remove(name)
            except FileNotFoundError:
                pass


class FileCache(_FileCacheMixin, BaseCache):
    """
    Traditional FileCache: body is stored in memory, so not suitable for large
    downloads.
    """

    def delete(self, key: str) -> None:
        self._delete(key, "")


class SeparateBodyFileCache(_FileCacheMixin, SeparateBodyBaseCache):
    """
    Memory-efficient FileCache: body is stored in a separate file, reducing
    peak memory usage.
    """

    def get_body(self, key: str) -> t.IO[bytes] | None:
        name = self._fn(key) + ".body"
        try:
            return open(name, "rb")
        except FileNotFoundError:
            return None

    def set_body(self, key: str, body: bytes) -> None:
        name = self._fn(key) + ".body"
        self._write(name, body)

    def delete(self, key: str) -> None:
        self._delete(key, "")
        self._delete(key, ".body")


def url_to_file_path(url: str, filecache: FileCache) -> str:
    """Return the file cache path based on the URL.

    This does not ensure the file exists!
    """
    key = CacheController.cache_url(url)
    return filecache._fn(key)
