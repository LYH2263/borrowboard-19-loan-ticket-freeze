"""Loan/recall mutations. Every write runs in one BEGIN IMMEDIATE transaction with
guarded UPDATEs (rowcount checked) so racing confirm / return / cancel / overdue-scan
reads can only ever produce one loans.status outcome."""
import sqlite3
from datetime import datetime, timezone
from app.db import connect
from app.engines.borrow_rules import (
    can_lend, can_initiate_recall, can_resolve_recall, check_recall_effect,
)

class DomainError(Exception):
    def __init__(self, reason: str, status: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status = status

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def lend_item(item_id: int, borrower: str, due_date: str) -> int:
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not item:
            raise DomainError("item", 404)
        active = c.execute(
            "SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (item_id,)).fetchone()["c"]
        check = can_lend(item["status"], active)
        if not check["ok"]:
            raise DomainError(check["reason"])
        cur = c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (item_id, borrower, "active", due_date, _now()))
        c.execute("UPDATE items SET status='on_loan' WHERE id=? AND status='available'", (item_id,))
        c.commit()
        return cur.lastrowid
    except DomainError:
        c.rollback(); raise
    finally:
        c.close()

def return_loan(loan_id: int) -> None:
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        loan = c.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            raise DomainError("loan", 404)
        n = c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=? AND status='active'",
                      (_now(), loan_id)).rowcount
        if n != 1:
            raise DomainError("not_active", 400)
        c.execute("UPDATE items SET status='available' WHERE id=? AND status='on_loan'", (loan["item_id"],))
        # 借用人自还：挂在该在借上的未完成工单随之了结，收回名单与在借栏保持一致
        c.execute("UPDATE recalls SET status='done', resolved_at=? WHERE loan_id=? AND status='open'",
                  (_now(), loan_id))
        c.commit()
    except DomainError:
        c.rollback(); raise
    finally:
        c.close()

def initiate_recall(loan_id: int, effect: str) -> int:
    check = check_recall_effect(effect)
    if not check["ok"]:
        raise DomainError(check["reason"], 400)
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        loan = c.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            raise DomainError("loan", 404)
        open_n = c.execute(
            "SELECT COUNT(*) c FROM recalls WHERE loan_id=? AND status='open'", (loan_id,)).fetchone()["c"]
        check = can_initiate_recall(loan["status"], open_n)
        if not check["ok"]:
            raise DomainError(check["reason"])
        owner = c.execute("SELECT owner FROM items WHERE id=?", (loan["item_id"],)).fetchone()
        # 只插工单：在借行保持 active，due_date 不被改写，物品保持 on_loan 不回可借栏
        cur = c.execute(
            "INSERT INTO recalls(loan_id,item_id,owner,effect,status,created_at) VALUES (?,?,?,?,?,?)",
            (loan_id, loan["item_id"], owner["owner"] if owner else "", effect, "open", _now()))
        c.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:  # 唯一开放工单索引兜底并发双击
        c.rollback(); raise DomainError("recall_already_open")
    except DomainError:
        c.rollback(); raise
    finally:
        c.close()

def confirm_recall(recall_id: int) -> str:
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT * FROM recalls WHERE id=?", (recall_id,)).fetchone()
        if not r:
            raise DomainError("recall", 404)
        check = can_resolve_recall(r["status"])
        if not check["ok"]:
            raise DomainError(check["reason"])
        n = c.execute("UPDATE recalls SET status='done', resolved_at=? WHERE id=? AND status='open'",
                      (_now(), recall_id)).rowcount
        if n != 1:
            raise DomainError("recall_not_open")
        if r["effect"] == "close_return":
            # 当场结还：与借用人归还撞车时只有一方能翻转 loans.status
            n = c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=? AND status='active'",
                          (_now(), r["loan_id"])).rowcount
            if n != 1:
                raise DomainError("loan_not_active")
            c.execute("UPDATE items SET status='available' WHERE id=? AND status='on_loan'", (r["item_id"],))
        # 只催：在借行与物品状态不动，等借用人自还
        c.commit()
        return r["effect"]
    except DomainError:
        c.rollback(); raise
    finally:
        c.close()

def cancel_recall(recall_id: int) -> None:
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT * FROM recalls WHERE id=?", (recall_id,)).fetchone()
        if not r:
            raise DomainError("recall", 404)
        check = can_resolve_recall(r["status"])
        if not check["ok"]:
            raise DomainError(check["reason"])
        n = c.execute("UPDATE recalls SET status='cancelled', resolved_at=? WHERE id=? AND status='open'",
                      (_now(), recall_id)).rowcount
        if n != 1:
            raise DomainError("recall_not_open")
        c.commit()
    except DomainError:
        c.rollback(); raise
    finally:
        c.close()
