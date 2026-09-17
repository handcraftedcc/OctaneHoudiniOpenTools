"""Windows path helpers used by the Botaniq export/import pipeline.

Blender may expand a mapped drive to its UNC name while Houdini is running.
When the same share is mapped in the current Windows session, using the drive
letter is more portable between Blender and Houdini.  The mapping is queried
from Windows at runtime; no server name or drive letter is embedded here.
"""
from __future__ import absolute_import

import ctypes
import ntpath
import os
from ctypes import wintypes


def _text(value):
    try:
        return os.fspath(value)
    except AttributeError:
        return str(value)


def _normal_unc(value):
    """Return a case-insensitive UNC prefix suitable for matching."""
    value = _text(value).replace('/', '\\')
    while len(value) > 2 and value.endswith('\\'):
        value = value[:-1]
    return value.casefold()


def mapped_drive_connections():
    """Return ``[(drive, unc_root), ...]`` for mapped drives in this session.

    This intentionally returns an empty list outside Windows and when the
    network provider is unavailable.  Callers then retain their original
    path rather than guessing a drive letter.
    """
    if os.name != 'nt':
        return []
    try:
        mpr = ctypes.WinDLL('mpr.dll')
        get_connection = mpr.WNetGetConnectionW
        get_connection.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
                                   ctypes.POINTER(wintypes.DWORD)]
        get_connection.restype = wintypes.DWORD
    except (AttributeError, OSError):
        return []

    connections = []
    for index in range(26):
        drive = '{}:'.format(chr(ord('A') + index))
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        try:
            result = get_connection(drive, buffer, ctypes.byref(size))
        except (OSError, TypeError):
            continue
        # NO_ERROR.  Other values include ERROR_NOT_CONNECTED for local drives.
        if result == 0 and buffer.value.startswith('\\\\'):
            connections.append((drive, buffer.value))
    return connections


def remap_unc_to_drive(path, mappings=None):
    """Replace a UNC path with the longest matching mapped drive prefix."""
    original = _text(path)
    if not original.replace('/', '\\').startswith('\\\\'):
        return original
    mappings = mapped_drive_connections() if mappings is None else mappings
    normalized = _normal_unc(original)
    best = None
    for drive, unc_root in mappings:
        prefix = _normal_unc(unc_root)
        if normalized == prefix or normalized.startswith(prefix + '\\'):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, _text(drive).rstrip('\\/'), _text(unc_root))
    if best is None:
        return original
    prefix, drive, unc_root = best
    # The normalized prefix has the same length as the original UNC root
    # after slash normalization, so the original suffix can be preserved.
    root_length = len(_text(unc_root).replace('/', '\\').rstrip('\\'))
    suffix = original.replace('/', '\\')[root_length:]
    return drive + (suffix or '\\')


def resolve_path(folder, path, mappings=None):
    """Join a package folder and texture path, then apply drive remapping."""
    folder_text, path_text = _text(folder), _text(path)
    has_drive = len(path_text) > 1 and path_text[1] == ':'
    folder_has_drive = len(folder_text) > 1 and folder_text[1] == ':'
    posix_style = ((folder_text.startswith('/') and not folder_text.startswith('//'))
                   or (path_text.startswith('/') and not path_text.startswith('//')))
    windows_style = (not posix_style and (path_text.startswith(('\\\\', '//')) or has_drive
                     or folder_text.startswith(('\\\\', '//')) or folder_has_drive
                     or (os.name == 'nt' and ntpath.isabs(path_text))))
    if windows_style:
        joined = path_text if ntpath.isabs(path_text) else ntpath.join(folder_text, path_text)
        joined = ntpath.normpath(joined)
    else:
        joined = path_text if os.path.isabs(path_text) else os.path.normpath(os.path.join(folder_text, path_text))
    return remap_unc_to_drive(joined, mappings)


def path_key(path):
    """Stable comparison key that treats a mapped drive and its UNC as equal."""
    value = remap_unc_to_drive(_text(path))
    if ntpath.isabs(value) or len(value) > 1 and value[1] == ':':
        return ntpath.normcase(ntpath.normpath(value))
    return os.path.normcase(os.path.abspath(value))


def exists(folder, path, mappings=None):
    """Check a package-relative or absolute path after remapping."""
    return os.path.isfile(resolve_path(folder, path, mappings))
