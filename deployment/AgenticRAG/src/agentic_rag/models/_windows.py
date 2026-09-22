"""Small native Windows ownership helpers; no shell or optional dependency."""

import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import re

k = c.WinDLL('kernel32', use_last_error=True)
a = c.WinDLL('advapi32', use_last_error=True)
s = c.WinDLL('shell32', use_last_error=True)
k.GetCurrentProcess.restype = w.HANDLE
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
k.LocalFree.argtypes = [c.c_void_p]
a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
a.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
a.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p]
a.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)]
a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.c_void_p]
k.GetProcessTimes.argtypes = [w.HANDLE, c.POINTER(w.FILETIME), c.POINTER(w.FILETIME), c.POINTER(w.FILETIME), c.POINTER(w.FILETIME)]


def checked(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def user_sid():
    token = w.HANDLE()
    checked(a.OpenProcessToken(k.GetCurrentProcess(), 8, c.byref(token)))
    try:
        size = w.DWORD()
        a.GetTokenInformation(token, 1, None, 0, c.byref(size))
        buf = c.create_string_buffer(size.value)
        checked(a.GetTokenInformation(token, 1, buf, size, c.byref(size)))
        sid = c.cast(buf, c.POINTER(c.c_void_p))[0]
        string = w.LPWSTR()
        checked(a.ConvertSidToStringSidW(sid, c.byref(string)))
        try:
            return string.value
        finally:
            k.LocalFree(string)
    finally:
        k.CloseHandle(token)


def local_appdata():
    # CSIDL_LOCAL_APPDATA from the current user profile, not caller env variables.
    buf = c.create_unicode_buffer(32768)
    if s.SHGetFolderPathW(None, 28, None, 0, buf):
        raise OSError('cannot resolve current user local application directory')
    return Path(buf.value)


class SecurityAttributes(c.Structure):
    _fields_ = [('length', w.DWORD), ('descriptor', c.c_void_p), ('inherit', w.BOOL)]


k.CreateDirectoryW.argtypes = [w.LPCWSTR, c.POINTER(SecurityAttributes)]


def private_mkdir(path):
    descriptor = c.c_void_p()
    sid = user_sid()
    checked(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f'O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)', 1, c.byref(descriptor), None))
    try:
        attributes = SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
        if not k.CreateDirectoryW(str(path), c.byref(attributes)) and c.get_last_error() != 183:
            raise c.WinError(c.get_last_error())
    finally:
        k.LocalFree(descriptor)
    verify_private(path)


def verify_private(path):
    descriptor = c.c_void_p()
    error = a.GetNamedSecurityInfoW(str(path), 1, 5, None, None, None, None, c.byref(descriptor))
    if error:
        raise c.WinError(error)
    string = w.LPWSTR()
    try:
        checked(a.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 5, c.byref(string), None))
        value = string.value
        owner = value.split('O:', 1)[1].split('D:', 1)[0]
        aces = re.findall(r'\(([^()]*)\)', value)
        if owner != user_sid() or not aces:
            raise PermissionError('runtime owner or ACL is not private')
        for ace in aces:
            fields = ace.split(';')
            if len(fields) != 6 or fields[0] != 'A' or fields[5] not in (user_sid(), 'SY'):
                raise PermissionError('runtime ACL admits another principal')
        return {'owner_current_user': True, 'allowed_principals': ['current_user', 'SYSTEM']}
    finally:
        if string:
            k.LocalFree(string)
        k.LocalFree(descriptor)


def process_birth(pid):
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        if c.get_last_error() == 87:
            return None
        raise c.WinError(c.get_last_error())
    try:
        created, exited, kernel, user = (w.FILETIME() for _ in range(4))
        checked(k.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(kernel), c.byref(user)))
        # A process object may still exist after exit; use exit time as evidence.
        if exited.dwHighDateTime or exited.dwLowDateTime:
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        k.CloseHandle(handle)


def boot_id():
    class BootInfo(c.Structure):
        _fields_ = [('identifier', c.c_byte * 16), ('firmware', w.DWORD), ('flags', c.c_ulonglong)]
    info = BootInfo()
    ntdll = c.WinDLL('ntdll')
    status = ntdll.NtQuerySystemInformation(90, c.byref(info), c.sizeof(info), None)
    if status != 0:
        raise OSError('cannot verify Windows boot clock domain')
    return bytes(info.identifier).hex()
