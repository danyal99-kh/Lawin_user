def session_dict(s, table=None):
    if s is None:
        return None
    t = table or s.table
    return {"id": str(s.id), "table_id": t.id, "table_number": t.number,
            "entered_at": s.entered_at.isoformat(),
            "exited_at": s.exited_at.isoformat() if s.exited_at else None}


def table_dict(t):
    return {"id": t.id, "number": t.number, "status": t.status}
