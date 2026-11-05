"""Provider errors safe to surface in logs (never contain credentials)."""


class ProviderError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
