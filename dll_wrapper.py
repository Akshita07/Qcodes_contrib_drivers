"""
This module has some convenience classes and functions for wrapping NI C API
calls. Modeled after the DLL calls in the NIMI-python library
(see e.g.
https://github.com/ni/nimi-python/blob/master/generated/nitclk/_library.py).
"""

import ctypes
from ctypes import POINTER
from typing import Optional, List, Any, Callable
import warnings
from dataclasses import dataclass
from .visa_types import (
    ViChar, ViStatus, ViRsrc, ViInt32, ViString, ViSession, ViBoolean, ViAttr,
    ViChar, ViReal64, VI_NULL
)

# 256 bytes should be enough, according to the documentation
STRING_BUFFER_SIZE = 257


def c_str(s: str): return bytes(s, "ascii")


@dataclass
class AttributeWrapper(object):
    """
    Struct to associate a data type to a numeric constant (i.e. attribute)
    defined in a NI DLL library. `dtype` should be one of the types defined in
    the `visa_types` module. Here, `value` means the same as the attributeID in
    the DLL documentation.
    """
    value: ViAttr
    dtype: Any


@dataclass
class NamedArgType:
    """
    Struct for associating a name with an argument type for DLL functions.
    """
    name: str
    argtype: Any


class NIDLLWrapper(object):
    """
    This class provides convenience functions for wrapping and checking a DLL
    function call, as well as some premade pythonic wrapper functinos for
    common library functions such as libName_error_message, libName_init/close
    and libName_GetAttribute, (e.g. niRFSG_init or niSync_init). Other
    functions should be wrapped by a library-specific class by calling
    wrap_dll_function_checked.

    Args:
        dll_path: path to the DLL file containing the library
        lib_prefix: All function names in the library start with this. For
            example, for NI-RFSG, where function names are of the form
            niRFSG_name, `lib_prefix` should be 'niRFSG'
    """

    def __init__(self, dll_path: str, lib_prefix: str):
        self._dll = ctypes.cdll.LoadLibrary(dll_path)
        self._lib_prefix = lib_prefix

        # list of function names registered with setatttr
        self._wrapped_functions: List[str] = []

        self._dtype_map = {
                ViBoolean: "ViBoolean",
                ViInt32: "ViInt32",
                ViReal64: "ViReal64",
                #ViString: "ViString", # strings not implemented
        }

        # standard functions that are the same in all libraries
        self.wrap_dll_function(name_in_library="error_message",
                               # note special name, self.error_message is a
                               # convenience wrapper around this
                               name="_error_message",
                               argtypes=[
                                   NamedArgType("vi", ViSession),
                                   NamedArgType("errorCode", ViStatus),
                                   NamedArgType("errorMessage",
                                                POINTER(ViChar)),
                               ])

        self.wrap_dll_function_checked(name_in_library="init",
                                       # this is wrapped in self.init
                                       name="_init",
                                       argtypes=[
                                           NamedArgType("resourceName",
                                                        ViRsrc),
                                           NamedArgType("idQuery", ViBoolean),
                                           NamedArgType("resetDevice",
                                                        ViBoolean),
                                       ])

        self.wrap_dll_function_checked(name_in_library="reset",
                                       # no special name is needed,
                                       # the signature is the same
                                       argtypes=[
                                           NamedArgType("vi", ViSession),
                                       ])

        self.wrap_dll_function_checked(name_in_library="close",
                                       argtypes=[
                                           NamedArgType("vi", ViSession),
                                       ])

        for dtype, dtype_name in self._dtype_map.items():
            if dtype == ViString:
                continue

            self.wrap_dll_function_checked(f"GetAttribute{dtype_name}",
                                           argtypes=[
                                               NamedArgType("vi", ViSession),
                                               NamedArgType("channelName",
                                                            ViString),
                                               NamedArgType("attributeID",
                                                            ViAttr),
                                               NamedArgType("attributeValue",
                                                            POINTER(dtype))
                                           ])

    def wrap_dll_function(self, name_in_library: str,
                          argtypes: List[NamedArgType],
                          restype: Any = ViStatus,
                          name: Optional[str] = None) -> None:
        """
        Convenience function for wrapping a function in a NI C API.

        Args:
            name_in_library: The name of the function in the library (e.g.
                "niRFSG_init", or without the prefix, just "init")
            name: The name of the method that will be registered. For example,
                if `name = "func"`, you can then call `NI_RFSG.func()`. If
                None, `name_in_library` will be used instead.
            argtypes: list of NamedArgType containing the names and types
                of the arguments of the function
            restype: The return type of the library function (most likely
                ViStatus)
        """
        name, func = self._wrap_c_func_attributes(
                         name_in_library=name_in_library,
                         argtypes=argtypes,
                         restype=restype,
                         name=name)

        self._wrapped_functions.append(name)
        setattr(self, name, func)

    def wrap_dll_function_checked(self, name_in_library: str,
                                  argtypes: List[NamedArgType],
                                  name: Optional[str] = None) -> None:
        """
        Same as `wrap_dll_function`, but check the return value and raise an
        exception if the return value indicates an error or warn if it's a
        warning. Calls `self.error_message`, which must be initialized with
        `wrap_dll_function` before this function can be used. The arguments are
        the same as for `wrap_dll_function`, except that `restype` is always
        `ViStatus`.
        """
        if not getattr(self, "error_message"):
            raise RuntimeError(("wrap_dll_function_checked: self.error_message"
                                " not found, wrap it with wrap_dll_function"
                                " before calling wrap_dll_function_checked."))

        name, func = self._wrap_c_func_attributes(
                         name_in_library=name_in_library,
                         argtypes=argtypes,
                         restype=ViSession,
                         name=name)

        def func_checked(*args, **kwargs):
            error_code = func(*args, **kwargs)
            if error_code != 0:
                msg = self.error_message(error_code=error_code)
                if error_code < 0:
                    # negative error codes are errors
                    raise RuntimeError(msg)
                else:
                    warnings.warn(f"({error_code}) {msg}", RuntimeWarning,
                                  stacklevel=3)

        # annotate function so that it is compatible with ctypes func pointer
        func_checked.restype = func.restype
        func_checked.argtypes = func.argtypes
        func_checked.argnames = func.argnames

        self._wrapped_functions.append(name)
        setattr(self, name, func_checked)

    def _wrap_c_func_attributes(self, name_in_library: str,
                                argtypes: List[NamedArgType],
                                restype: Any,
                                name: Optional[str] = None) -> (str, Callable):
        """
        Helper method for `wrap_dll_function` and `wrap_dll_function_checked.`
        """

        if name is None:
            name = name_in_library

        if not name_in_library.startswith(self._lib_prefix):
            name_in_library = f"{self._lib_prefix}_{name_in_library}"

        # TODO: lock? (see nimi-python link at top of file)
        func = getattr(self._dll, name_in_library)
        func.restype = restype
        func.argtypes = [a.argtype for a in argtypes]
        func.argnames = [a.name for a in argtypes]  # just in case

        return name, func

    def init(self, resource: str, id_query: bool = True,
             reset_device: bool = False) -> ViSession:
        """
        Convenience wrapper around libName_init (e.g. niRFSG_init). Returns the
        ViSession handle. The wrapped version of the actual DLL function is
        registered as self._init, see __init__. Note that this class is not
        responsible for storing the handle.

        Args:
            resource: the resource name of the device to initialize, as given
                by NI MAX.
            id_query: whether to perform an ID query
            reset_device: whether to reset the device during initialization
        Returns:
            the ViSession handle of the initialized device
        """
        session = ViSession()
        self._init(ViRsrc(c_str(resource)), id_query, reset_device,
                   ctypes.byref(session))
        return session

    def get_attribute(self, session: ViSession, attr: AttributeWrapper) -> Any:
        """
        Map an attribute with data type DataType to the appropriate
        "libName_GetAttributeDataType" function (for example
        niRFSG_GetAttributeViReal64 when lib_prefix is niRFSG and attr.dtype is
        ViReal64).

        NOTE: channels are not implemented.
        """
        dtype = attr.dtype
        if dtype not in self._dtype_map:
            raise ValueError(f"get_attribute() not implemented for {dtype}")

        dtype_name = self._dtype_map[dtype]
        func = getattr(self, f"GetAttribute{dtype_name}")
        res = dtype()
        func(session, b"", attr.value, ctypes.byref(res))
        return res.value

    def error_message(self, session: Optional[ViSession] = None,
                      error_code: ViStatus = 0):
        """
        Convenience wrapper around libName_error_message (which is registered
        as self._error_message).
        """
        buf = ctypes.create_string_buffer(STRING_BUFFER_SIZE)
        self._error_message(session or VI_NULL, error_code, buf)
        return buf.value.decode()
