import sqlite3
import pytest
from app import seed, store
from app.db import connect

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()

def _new_loan(borrower="邻居乙", due="2030-01-01"):
    c = connect()
    cur = c.execute("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
                    ("电钻", "老周", "available", "clean"))
    iid = cur.lastrowid; c.commit(); c.close()
    return iid, store.lend_item(iid, borrower, due)

def _one(sql, args=()):
    c = connect(); r = c.execute(sql, args).fetchone(); c.close(); return r

def _loan(lid): return _one("SELECT * FROM loans WHERE id=?", (lid,))
def _item(iid): return _one("SELECT * FROM items WHERE id=?", (iid,))
def _recall(rid): return _one("SELECT * FROM recalls WHERE id=?", (rid,))
def _open_recalls(lid):
    c = connect()
    n = c.execute("SELECT COUNT(*) c FROM recalls WHERE loan_id=? AND status='open'", (lid,)).fetchone()["c"]
    c.close(); return n

def test_initiate_keeps_loan_active_and_item_on_loan(db):
    iid, lid = _new_loan()
    rid = store.initiate_recall(lid, "close_return")
    assert _recall(rid)["status"] == "open"
    loan = _loan(lid)
    assert loan["status"] == "active" and loan["due_date"] == "2030-01-01"
    assert _item(iid)["status"] == "on_loan"  # 可借栏仍不得出现

def test_initiate_failure_rolls_back(db):
    iid, lid = _new_loan()
    store.initiate_recall(lid, "remind")
    before = _open_recalls(lid)
    with pytest.raises(store.DomainError) as e:
        store.initiate_recall(lid, "close_return")  # 同一在借第二张未完成工单
    assert e.value.reason == "recall_already_open"
    assert _open_recalls(lid) == before             # 条数回到点下去之前
    assert _loan(lid)["due_date"] == "2030-01-01"   # 空工单不改写 due_date

def test_initiate_rejects_bad_effect_and_inactive(db):
    iid, lid = _new_loan()
    with pytest.raises(store.DomainError) as e:
        store.initiate_recall(lid, "")
    assert e.value.status == 400
    assert _open_recalls(lid) == 0
    store.return_loan(lid)
    with pytest.raises(store.DomainError) as e:
        store.initiate_recall(lid, "remind")
    assert e.value.reason == "loan_not_active"

def test_unique_open_index_backstop(db):
    _, lid = _new_loan()
    store.initiate_recall(lid, "remind")
    c = connect()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO recalls(loan_id,item_id,owner,effect,status,created_at) VALUES (?,?,?,?,?,?)",
                  (lid, 1, "老周", "remind", "open", "now"))
    c.close()

def test_confirm_close_return_flips_once(db):
    iid, lid = _new_loan()
    rid = store.initiate_recall(lid, "close_return")
    assert store.confirm_recall(rid) == "close_return"
    assert _loan(lid)["status"] == "returned"
    assert _item(iid)["status"] == "available"      # 回可借栏
    assert _recall(rid)["status"] == "done"
    with pytest.raises(store.DomainError) as e:
        store.return_loan(lid)                       # 借用人归还撞车：loans.status 只有一种
    assert e.value.reason == "not_active"
    with pytest.raises(store.DomainError):
        store.confirm_recall(rid)                    # 重复确认

def test_confirm_remind_waits_for_self_return(db):
    iid, lid = _new_loan()
    rid = store.initiate_recall(lid, "remind")
    assert store.confirm_recall(rid) == "remind"
    assert _loan(lid)["status"] == "active"          # 只催：在借行不动
    assert _item(iid)["status"] == "on_loan"
    assert _recall(rid)["status"] == "done"
    store.return_loan(lid)                           # 借用人自还
    assert _loan(lid)["status"] == "returned"
    assert _item(iid)["status"] == "available"

def test_cancel_then_confirm_race(db):
    iid, lid = _new_loan()
    rid = store.initiate_recall(lid, "close_return")
    store.cancel_recall(rid)
    assert _recall(rid)["status"] == "cancelled"
    assert _loan(lid)["status"] == "active"          # 撤回不动在借
    with pytest.raises(store.DomainError) as e:
        store.confirm_recall(rid)                    # 撤回与确认撞车
    assert e.value.reason == "recall_not_open"
    with pytest.raises(store.DomainError):
        store.cancel_recall(rid)                     # 物主再点撤回
    store.return_loan(lid)                           # 在借仍可正常归还
    assert _loan(lid)["status"] == "returned"

def test_borrower_return_auto_resolves_open_recall(db):
    iid, lid = _new_loan()
    rid = store.initiate_recall(lid, "close_return")
    store.return_loan(lid)                           # 借用人先还了
    assert _recall(rid)["status"] == "done"
    with pytest.raises(store.DomainError):
        store.confirm_recall(rid)                    # 确认撞车：工单已了结
    loan = _loan(lid)
    assert loan["status"] == "returned"              # 唯一的 loans.status 结果
    assert _item(iid)["status"] == "available"

def test_lend_blocked_while_recall_open(db):
    iid, lid = _new_loan()
    store.initiate_recall(lid, "remind")
    with pytest.raises(store.DomainError) as e:
        store.lend_item(iid, "邻居丙", "2030-02-01")  # 没还时邻居直接借出必须失败
    assert e.value.reason in ("item_not_available", "already_on_loan")
    assert _loan(lid)["status"] == "active"
