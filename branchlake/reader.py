from .corrections import rows


def read(catalog, snapshot_id):
    """Rows of a fixed snapshot in the original public nested form."""
    return [row for _, row in rows(catalog, snapshot_id)]
