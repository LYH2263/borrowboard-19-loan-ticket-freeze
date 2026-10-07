import sqlite3
from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.borrow_rules import can_lend, classify_loans
from app.modules.recall import EFFECTS, OPEN, DONE, CANCELLED, can_open_recall, confirm_plan

app = FastAPI(title="Borrowboard", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 在借/逾期行带上未完成收回工单（partial unique index 保证至多一条，LEFT JOIN 不会扇出）
LOANS_SQL = """
    SELECT loans.*, items.title, recalls.id AS recall_id, recalls.effect AS recall_effect
    FROM loans
    JOIN items ON items.id = loans.item_id
    LEFT JOIN recalls ON recalls.loan_id = loans.id AND recalls.status = 'open'
"""

RECALLS_SQL = """
    SELECT recalls.*, loans.borrower, loans.due_date, loans.status AS loan_status,
           items.title, items.owner
    FROM recalls
    JOIN loans ON loans.id = recalls.loan_id
    JOIN items ON items.id = recalls.item_id
"""

@app.on_event("startup")
def _startup(): seed.init_db()

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/board")
def board():
    c = connect()
    available = [dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")]
    loans = [dict(r) for r in c.execute(LOANS_SQL + " WHERE loans.status='active'")]
    recalls = [dict(r) for r in c.execute(RECALLS_SQL + " WHERE recalls.status='open' ORDER BY recalls.id DESC")]
    c.close()
    cls = classify_loans(loans, date.today().isoformat())
    return {
        "available": available,
        "active": cls["active"],
        "overdue": cls["overdue"],
        "recalls": recalls,
        "counts": {"available": len(available), "active": len(cls["active"]),
                   "overdue": len(cls["overdue"]), "recalls": len(recalls)},
    }

class ItemIn(BaseModel):
    title: str
    owner: str

@app.post("/api/items")
def add_item(body: ItemIn):
    c = connect()
    cur = c.execute("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
                    (body.title, body.owner, "available", "clean"))
    c.commit(); iid = cur.lastrowid; c.close(); return {"id": iid}

class LendIn(BaseModel):
    borrower: str
    due_date: str

@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    c = connect()
    try:
        item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        if not item: raise HTTPException(404, "item")
        active = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
        check = can_lend(item["status"], active)
        if not check["ok"]:
            raise HTTPException(409, check["reason"])
        # 原子门闩：借用人未还（含挂着收回工单）时物件不是 available，邻居直接借出必失败
        gate = c.execute("UPDATE items SET status='on_loan' WHERE id=? AND status='available'", (iid,))
        if gate.rowcount == 0:
            c.rollback(); raise HTTPException(409, "item_not_available")
        cur = c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (iid, body.borrower, "active", body.due_date, _now()))
        c.commit(); return {"loan_id": cur.lastrowid}
    finally:
        c.close()

@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    c = connect()
    now = _now()
    try:
        # 条件更新：与收回确认撞车时只允许一种 loans.status 落库
        cur = c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=? AND status='active'",
                        (now, lid))
        if cur.rowcount == 0:
            exists = c.execute("SELECT 1 FROM loans WHERE id=?", (lid,)).fetchone()
            c.rollback()
            if not exists: raise HTTPException(404, "loan")
            raise HTTPException(400, "not_active")
        loan = c.execute("SELECT item_id FROM loans WHERE id=?", (lid,)).fetchone()
        c.execute("UPDATE items SET status='available' WHERE id=?", (loan["item_id"],))
        # 借用人自还：该在借上的未完成收回工单一并了结
        c.execute("UPDATE recalls SET status=?, closed_at=? WHERE loan_id=? AND status='open'",
                  (DONE, now, lid))
        c.commit(); return {"ok": True}
    finally:
        c.close()

@app.get("/api/loans")
def loans():
    c = connect()
    rows = [dict(r) for r in c.execute(LOANS_SQL + " ORDER BY loans.id DESC")]
    c.close()
    return classify_loans(rows, date.today().isoformat())

class RecallIn(BaseModel):
    effect: str
    note: str | None = None

@app.post("/api/loans/{lid}/recalls")
def open_recall(lid: int, body: RecallIn):
    if body.effect not in EFFECTS:
        raise HTTPException(400, "bad_effect")
    c = connect()
    try:
        loan = c.execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
        if not loan: raise HTTPException(404, "loan")
        open_n = c.execute("SELECT COUNT(*) c FROM recalls WHERE loan_id=? AND status='open'",
                           (lid,)).fetchone()["c"]
        check = can_open_recall(loan["status"], open_n)
        if not check["ok"]:
            raise HTTPException(409, check["reason"])
        # 只插工单：在借保持 active、due_date 不被改写；失败整体回滚，不留空工单
        try:
            cur = c.execute(
                "INSERT INTO recalls(loan_id,item_id,effect,status,note,created_at) VALUES (?,?,?,'open',?,?)",
                (lid, loan["item_id"], body.effect, body.note, _now()))
            c.commit()
        except sqlite3.IntegrityError:
            c.rollback(); raise HTTPException(409, "recall_already_open")
        return {"recall_id": cur.lastrowid}
    finally:
        c.close()

@app.get("/api/recalls")
def recalls(status: str | None = None):
    c = connect()
    rows = [dict(r) for r in c.execute(RECALLS_SQL + " ORDER BY recalls.id DESC")]
    c.close()
    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows

@app.post("/api/recalls/{rid}/confirm")
def confirm_recall(rid: int):
    c = connect()
    now = _now()
    try:
        rec = c.execute("SELECT * FROM recalls WHERE id=?", (rid,)).fetchone()
        if not rec: raise HTTPException(404, "recall")
        if rec["status"] != OPEN: raise HTTPException(409, "recall_not_open")
        plan = confirm_plan(rec["effect"])
        if plan["close_loan"]:
            # settle 当场结还：与借用人归还/逾期后的其它写入撞车时只允许一种 loans.status
            cur = c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=? AND status='active'",
                            (now, rec["loan_id"]))
            if cur.rowcount == 0:
                c.rollback(); raise HTTPException(409, "loan_not_active")
            c.execute("UPDATE items SET status=? WHERE id=?", (plan["item_status"], rec["item_id"]))
        # nudge 只催：不动在借与物件，等待借用人自还
        done = c.execute("UPDATE recalls SET status=?, closed_at=? WHERE id=? AND status='open'",
                         (DONE, now, rid))
        if done.rowcount == 0:
            c.rollback(); raise HTTPException(409, "recall_not_open")
        c.commit()
        return {"ok": True, "effect": rec["effect"], "loan_closed": plan["close_loan"]}
    finally:
        c.close()

@app.post("/api/recalls/{rid}/cancel")
def cancel_recall(rid: int):
    c = connect()
    try:
        # 撤回只了结对工单本身，不碰在借；与确认撞车时条件更新保证只有一个赢家
        cur = c.execute("UPDATE recalls SET status=?, closed_at=? WHERE id=? AND status='open'",
                        (CANCELLED, _now(), rid))
        if cur.rowcount == 0:
            exists = c.execute("SELECT 1 FROM recalls WHERE id=?", (rid,)).fetchone()
            c.rollback()
            if not exists: raise HTTPException(404, "recall")
            raise HTTPException(409, "recall_not_open")
        c.commit(); return {"ok": True}
    finally:
        c.close()

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
