#!/usr/bin/env python
#
# A filesystem that maps a Python namespace to directories and files.
#
# Copyright (c) 2014 Murat Knecht
# License: MIT
#

from __future__ import print_function
from __future__ import absolute_import

import __builtin__
import errno
from itertools import chain, count
import os
import logging
import stat
import sys

import fuse

from pyfs.mapping import (
    add_module,
    add_symlink,
    is_dir,
    is_executable,
    is_file,
    is_symlink,
    get_content,
    get_elements,
    logcall,
    PATH_BIN_PREFIX,
    PATH_DOT_PREFIX,
    PATH_LIB_PREFIX,
    PATH_MODULES,
    read_from_string,
    reset_modules_list,
    CannotResolve,
)


class PyFS(fuse.Operations):

    def __init__(self, path_to_projectdir=None):
        super(PyFS, self).__init__()
        self._path_to_projectdir = path_to_projectdir
        self._next_fh = count()
        self._flags_for_open_files = {}  # file handle -> fh
        for name in ("__builtin__", "json", "os", "re", "string", "sys"):
            add_module(name)
        for name in dir(__builtin__):
            sourcepath = "{}{}".format(PATH_BIN_PREFIX, name)
            add_symlink(
                sourcepath,
                "{}{}{}/{}".format(
                    "../" * (sourcepath.count("/") - 1),
                    PATH_LIB_PREFIX[1:],
                    "__builtin__",
                    name
                )
            )
        self._log = logging.getLogger(self.__class__.__name__)

    @logcall
    def getattr(self, path, fh=None):
        try:
            return self.try_to_getattr(path, fh)
        except CannotResolve:
            raise fuse.FuseOSError(errno.ENOENT)

    @logcall
    def try_to_getattr(self, path, fh):
        if path == '/' or path == "/." or path == "/..":
            return dict(
                st_mode=stat.S_IFDIR | 0555,
                st_nlink=2,
            )
        elif path.startswith(PATH_DOT_PREFIX) and not "." in path:
            return dict(
                st_mode=stat.S_IFREG | 0555,
                st_nlink=1,
                st_size=len(get_content(path, self._path_to_projectdir)),
            )
        elif is_symlink(path):
            return dict(
                st_mode=stat.S_IFLNK | 0777,
                st_nlink=1,
                st_size=len(get_content(path, self._path_to_projectdir)),
            )
        elif is_dir(path):
            return dict(
                st_mode=stat.S_IFDIR | 0555,
                st_nlink=3,
            )
        elif is_file(path):
            def _get_file_mode():
                if path == PATH_MODULES:
                    return 0666
                elif is_executable(path):
                    return 0555
                else:
                    return 0444
            return dict(
                st_mode=stat.S_IFREG | _get_file_mode(),
                st_nlink=1,
                st_size=len(get_content(path, self._path_to_projectdir)),
            )
        else:
            raise fuse.FuseOSError(errno.ENOENT)

    @logcall
    def read(self, path, size, offset, fh):
        return read_from_string(
            get_content(path, self._path_to_projectdir),
            size,
            offset,
        )

    @logcall
    def readdir(self, path, fh):
        return [name for name in chain([".", ".."], get_elements(path))]

    @logcall
    def readlink(self, path):
        return get_content(path, self._path_to_projectdir)

    def open(self, path, flags):
        if path == PATH_MODULES:
            if flags & os.O_RDWR:
                self._log.debug(
                    "Cannot allow readwrite access. Flags: {}".format(flags))
                raise fuse.FuseOSError(errno.EPERM)
            if flags & os.O_TRUNC:
                reset_modules_list()
        else:
            if flags & os.O_WRONLY or flags & os.O_RDWR:
                self._log.debug(
                    "Cannot write to Python objects. Flags: {}".format(flags))
                raise fuse.FuseOSError(errno.EPERM)

        fh = self._next_fh.next()
        self._flags_for_open_files[fh] = flags
        return fh

    def truncate(self, path, length, fh=None):
        if path != PATH_MODULES:
            raise fuse.FuseOSError(errno.EPERM)
        if length != 0:
            self._log.debug("Must completely truncate the modules file.")
            raise IOError(errno.EPERM)
        reset_modules_list()

    def release(self, path, fh):
        if fh not in self._flags_for_open_files:
            # EBADFD = "File descriptor in bad state" (not sure it's correct)
            raise fuse.FuseOSError(errno.EBADFD)
        del self._flags_for_open_files[fh]
        return fh

    def write(self, path, data, offset, fh):
        if fh not in self._flags_for_open_files:
            # EBADFD = "File descriptor in bad state" (not sure it's correct)
            raise fuse.FuseOSError(errno.EBADFD)
        if not self._flags_for_open_files[fh] & os.O_APPEND and offset != 0:
            self._log.debug("Must either append to or truncate a file.")
            raise fuse.FuseOSError(-errno.EPERM)
        if data.strip():
            add_module(data.strip())
        return len(data)


if __name__ == '__main__':
    logging.basicConfig(filename="pyfs.log", filemode="w+")
    logging.getLogger().setLevel(logging.DEBUG)
    fuse.FUSE(PyFS(os.getcwd()), sys.argv[1])
