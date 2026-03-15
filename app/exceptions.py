"""
Domain Exceptions — Structured error hierarchy for the Flow QA API.
Replaces scattered HTTPException raises with typed, reusable exceptions.
"""
from fastapi import HTTPException, status


# ── Base ─────────────────────────────────────────────────────────────────

class FlowException(HTTPException):
    """Base exception for all Flow domain errors."""
    def __init__(self, detail: str, status_code: int = 500, headers: dict = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)


# ── Authentication & Authorization ────────────────────────────────────

class InvalidCredentialsError(FlowException):
    def __init__(self, detail: str = "Usuário ou senha incorretos"):
        super().__init__(detail=detail, status_code=status.HTTP_401_UNAUTHORIZED,
                         headers={"WWW-Authenticate": "Bearer"})


class TokenExpiredError(FlowException):
    def __init__(self, detail: str = "Token expirado ou inválido"):
        super().__init__(detail=detail, status_code=status.HTTP_401_UNAUTHORIZED,
                         headers={"WWW-Authenticate": "Bearer"})


class AccountPendingError(FlowException):
    def __init__(self, detail: str = "Cadastro pendente de aprovação. Contate o administrador."):
        super().__init__(detail=detail, status_code=status.HTTP_403_FORBIDDEN,
                         headers={"WWW-Authenticate": "Bearer"})


class InsufficientPermissionsError(FlowException):
    def __init__(self, detail: str = "Permissão insuficiente para esta ação"):
        super().__init__(detail=detail, status_code=status.HTTP_403_FORBIDDEN)


# ── Resource Errors ──────────────────────────────────────────────────

class NotFoundError(FlowException):
    def __init__(self, resource: str = "Recurso", identifier: str = ""):
        detail = f"{resource} não encontrado" + (f": {identifier}" if identifier else "")
        super().__init__(detail=detail, status_code=status.HTTP_404_NOT_FOUND)


class DuplicateResourceError(FlowException):
    def __init__(self, resource: str = "Recurso", detail: str = None):
        msg = detail or f"{resource} já existe"
        super().__init__(detail=msg, status_code=status.HTTP_409_CONFLICT)


# ── Validation & Business Logic ──────────────────────────────────────

class ValidationError(FlowException):
    def __init__(self, detail: str = "Dados inválidos"):
        super().__init__(detail=detail, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


class FlowExecutionError(FlowException):
    def __init__(self, detail: str = "Erro durante execução do fluxo"):
        super().__init__(detail=detail, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EmailSendError(FlowException):
    def __init__(self, detail: str = "Erro ao enviar e-mail"):
        super().__init__(detail=detail, status_code=status.HTTP_502_BAD_GATEWAY)
