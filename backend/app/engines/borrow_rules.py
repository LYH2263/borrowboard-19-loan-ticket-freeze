"""One active loan per item + overdue detection + owner recall work orders."""

RECALL_EFFECTS = ("close_return", "remind")  # 当场结还 / 只催

def can_lend(item_status: str, active_loans: int) -> dict:
    if item_status != "available":
        return {"ok": False, "reason": "item_not_available"}
    if active_loans > 0:
        return {"ok": False, "reason": "already_on_loan"}
    return {"ok": True, "reason": ""}

def is_overdue(due_date: str, today: str, loan_status: str) -> bool:
    if loan_status != "active":
        return False
    return bool(due_date) and due_date < today

def classify_loans(loans: list[dict], today: str) -> dict:
    active, overdue, returned = [], [], []
    for L in loans:
        st = L.get("status")
        if st == "returned":
            returned.append(L)
        elif is_overdue(L.get("due_date"), today, st):
            overdue.append({**L, "overdue": True})
        elif st == "active":
            active.append({**L, "overdue": False})
    return {"active": active, "overdue": overdue, "returned": returned}

def check_recall_effect(effect: str) -> dict:
    if effect not in RECALL_EFFECTS:
        return {"ok": False, "reason": "bad_effect"}
    return {"ok": True, "reason": ""}

def can_initiate_recall(loan_status: str, open_recalls: int) -> dict:
    if loan_status != "active":
        return {"ok": False, "reason": "loan_not_active"}
    if open_recalls > 0:
        return {"ok": False, "reason": "recall_already_open"}
    return {"ok": True, "reason": ""}

def can_resolve_recall(recall_status: str) -> dict:
    if recall_status != "open":
        return {"ok": False, "reason": "recall_not_open"}
    return {"ok": True, "reason": ""}
