class LakeError(Exception):
    """Base class for public business errors."""


class ValidationError(LakeError):
    pass


class NotFoundError(LakeError):
    pass


class ConflictError(LakeError):
    pass
