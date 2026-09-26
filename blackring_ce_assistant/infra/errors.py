# -*- coding: utf-8 -*-
"""统一应用错误类型。"""
from __future__ import annotations


class AppError(Exception):
    code = "app_error"

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover
        return f"[{self.code}] {self.message}"


class ProcessNotFound(AppError):
    code = "process_not_found"


class AttachDenied(AppError):
    code = "attach_denied"


class SymbolResolveError(AppError):
    code = "symbol_resolve_error"


class AddressInvalid(AppError):
    code = "address_invalid"


class WriteFailed(AppError):
    code = "write_failed"


class ValidationError(AppError):
    code = "validation_error"


class ProcessExited(AppError):
    code = "process_exited"


class NotAttached(AppError):
    code = "not_attached"


class ReadonlyMode(AppError):
    code = "readonly_mode"


class MappingError(AppError):
    code = "mapping_error"
