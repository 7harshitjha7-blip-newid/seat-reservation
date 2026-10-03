from contextvars import ContextVar


request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


def set_request_id(request_id: str):
    return request_id_context.set(request_id)


def reset_request_id(token):
    request_id_context.reset(token)


def get_request_id() -> str | None:
    return request_id_context.get()