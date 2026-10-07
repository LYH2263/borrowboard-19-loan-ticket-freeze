from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed, store
from app.db import connect
from app.engines.borrow_rules import classify_loans

app = FastAPI(title="Borrowboard", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

def _run(fn, *args):
    try:
        return fn(*args)
    except store.DomainError as e:
        raise HTTPException(e.status, e.reason)

LOAN_SELECT = """
    SELECT loans.*, items.title, recalls.id AS recall_id, recalls.effect AS recall_effect
    FROM loans JOIN items ON items.id=loans.item_id
    LEFT JOIN recalls ON recalls.loan_id=loans.id AND recalls.status='open'"""

@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/board")
def board():
    c = connect()
    available = [dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")]
    loans = [dict(r) for r in c.execute(LOAN_SELECT + " WHERE loans.status='active'")]
    recalls_open = c.execute("SELECT COUNT(*) c FROM recalls WHERE status='open'").fetchone()["c"]
    c.close()
    cls = classify_loans(loans, date.today().isoformat())
    return {
        "available": available,
        "active": cls["active"],
        "overdue": cls["overdue"],
        "counts": {"available": len(available), "active": len(cls["active"]),
                   "overdue": len(cls["overdue"]), "recalls_open": recalls_open},
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
    return {"loan_id": _run(store.lend_item, iid, body.borrower, body.due_date)}

@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    _run(store.return_loan, lid); return {"ok": True}

class RecallIn(BaseModel):
    effect: str  # close_return 当场结还 / remind 只催

@app.post("/api/loans/{lid}/recalls")
def initiate_recall(lid: int, body: RecallIn):
    return {"recall_id": _run(store.initiate_recall, lid, body.effect)}

@app.get("/api/recalls")
def recalls(status: str = "open"):
    q = """SELECT recalls.*, items.title, loans.borrower, loans.due_date, loans.status AS loan_status
           FROM recalls JOIN items ON items.id=recalls.item_id JOIN loans ON loans.id=recalls.loan_id"""
    c = connect()
    if status == "all":
        rows = [dict(r) for r in c.execute(q + " ORDER BY recalls.id DESC")]
    else:
        rows = [dict(r) for r in c.execute(q + " WHERE recalls.status=? ORDER BY recalls.id DESC", (status,))]
    c.close()
    return rows

@app.post("/api/recalls/{rid}/confirm")
def confirm_recall(rid: int):
    return {"effect": _run(store.confirm_recall, rid)}

@app.post("/api/recalls/{rid}/cancel")
def cancel_recall(rid: int):
    _run(store.cancel_recall, rid); return {"ok": True}

@app.get("/api/loans")
def loans():
    c = connect()
    rows = [dict(r) for r in c.execute(LOAN_SELECT + " ORDER BY loans.id DESC")]
    c.close()
    return classify_loans(rows, date.today().isoformat())

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
