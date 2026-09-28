def call_dict(c):
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": str(c.id), "table_id": c.table_id, "table_number": c.table.number, "status": c.status,
            "created_at": iso(c.created_at), "acknowledged_at": iso(c.acknowledged_at),
            "completed_at": iso(c.completed_at)}
