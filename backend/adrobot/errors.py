class AppError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class KeitaroError(AppError):
    def __init__(
        self, message: str, *, upstream_status: int | None = None, uncertain: bool = False
    ):
        super().__init__(503 if upstream_status in {401, 403} else 502, message)
        self.upstream_status = upstream_status
        self.uncertain = uncertain
