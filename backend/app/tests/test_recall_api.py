"""收回工单全链路测试：直接调用端点函数，DATA_DIR 指向临时库。"""
import threading

import pytest
from fastapi import HTTPException

from app import main, seed

# 种子数据：item 1 = 电钻（可借）；loan 1 = item 4 已外借样例，应还 2020-06-01（逾期）
DRILL_ID = 1
SEED_LOAN_ID = 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    yield tmp_path


def _lend_drill(due="2026-12-31"):
    return main.lend(DRILL_ID, main.LendIn(borrower="邻居甲", due_date=due))["loan_id"]


def _loan_row(lid):
    for group in ("active", "overdue", "returned"):
        for l in main.loans()[group]:
            if l["id"] == lid:
                return l
    return None


def _recall_row(rid):
    return next(r for r in main.recalls() if r["id"] == rid)


def test_open_recall_keeps_loan_active_and_item_unlisted(env):
    lid = _lend_drill()
    before = main.board()
    rid = main.open_recall(lid, main.RecallIn(effect="nudge"))["recall_id"]
    b = main.board()
    assert all(i["id"] != DRILL_ID for i in b["available"])  # 可借栏仍不出现
    loan = next(l for l in b["active"] if l["id"] == lid)
    assert loan["status"] == "active"                        # 在借行保持 active
    assert loan["due_date"] == "2026-12-31"                  # due_date 不被工单改写
    assert loan["recall_id"] == rid and loan["recall_effect"] == "nudge"
    assert len(b["recalls"]) == len(before["recalls"]) + 1   # 多一张工单
    assert b["counts"]["recalls"] == len(b["recalls"])       # 顶细条计数跟上


def test_lend_blocked_while_recall_pending(env):
    lid = _lend_drill()
    main.open_recall(lid, main.RecallIn(effect="settle"))
    with pytest.raises(HTTPException) as e:
        main.lend(DRILL_ID, main.LendIn(borrower="邻居乙", due_date="2027-01-01"))
    assert e.value.status_code == 409                        # 邻居直接借出必须失败


def test_confirm_settle_returns_item_to_available(env):
    lid = _lend_drill()
    rid = main.open_recall(lid, main.RecallIn(effect="settle"))["recall_id"]
    res = main.confirm_recall(rid)
    assert res["loan_closed"] is True
    b = main.board()
    assert any(i["id"] == DRILL_ID for i in b["available"])  # 回可借栏
    assert all(l["id"] != lid for l in b["active"] + b["overdue"])
    assert b["recalls"] == []                                # 工单了结，出收回名单
    assert _loan_row(lid)["status"] == "returned"
    assert _recall_row(rid)["status"] == "done"


def test_confirm_nudge_waits_for_borrower_self_return(env):
    lid = _lend_drill()
    rid = main.open_recall(lid, main.RecallIn(effect="nudge"))["recall_id"]
    res = main.confirm_recall(rid)
    assert res["loan_closed"] is False
    b = main.board()
    assert all(i["id"] != DRILL_ID for i in b["available"])  # 仍不回可借栏
    assert any(l["id"] == lid and l["status"] == "active" for l in b["active"])
    assert b["recalls"] == []
    main.return_loan(lid)                                    # 借用人随后自还
    b2 = main.board()
    assert any(i["id"] == DRILL_ID for i in b2["available"])


def test_borrower_return_closes_open_recall(env):
    lid = _lend_drill()
    rid = main.open_recall(lid, main.RecallIn(effect="nudge"))["recall_id"]
    main.return_loan(lid)
    rec = _recall_row(rid)
    assert rec["status"] == "done" and rec["loan_status"] == "returned"


def test_no_two_open_recalls_on_same_loan(env):
    lid = _lend_drill()
    main.open_recall(lid, main.RecallIn(effect="nudge"))
    n0 = len(main.recalls(status="open"))
    with pytest.raises(HTTPException) as e:
        main.open_recall(lid, main.RecallIn(effect="settle"))
    assert e.value.status_code == 409 and e.value.detail == "recall_already_open"
    assert len(main.recalls(status="open")) == n0            # 条数回到点下去之前


def test_failed_open_recall_leaves_no_trace(env):
    lid = _lend_drill(due="2026-12-31")
    main.return_loan(lid)
    with pytest.raises(HTTPException) as e:
        main.open_recall(lid, main.RecallIn(effect="settle"))  # 已还在借 → 409
    assert e.value.status_code == 409 and e.value.detail == "loan_not_active"
    assert main.recalls() == []                              # 不留空工单
    assert _loan_row(lid)["due_date"] == "2026-12-31"        # due_date 未被改写
    lid2 = _lend_drill()
    with pytest.raises(HTTPException) as e2:
        main.open_recall(lid2, main.RecallIn(effect="bogus"))  # 非法效果 → 400
    assert e2.value.status_code == 400
    assert main.recalls() == []


def test_cancel_recall_keeps_loan_active_and_is_single_shot(env):
    lid = _lend_drill()
    rid = main.open_recall(lid, main.RecallIn(effect="settle"))["recall_id"]
    main.cancel_recall(rid)
    b = main.board()
    assert any(l["id"] == lid and l["status"] == "active" for l in b["active"])
    assert all(i["id"] != DRILL_ID for i in b["available"])  # 撤回不放回可借栏
    assert _recall_row(rid)["status"] == "cancelled"
    with pytest.raises(HTTPException) as e:
        main.cancel_recall(rid)                              # 再点撤回 → 409
    assert e.value.status_code == 409


def test_confirm_after_cancel_conflicts(env):
    lid = _lend_drill()
    rid = main.open_recall(lid, main.RecallIn(effect="settle"))["recall_id"]
    main.cancel_recall(rid)
    with pytest.raises(HTTPException) as e:
        main.confirm_recall(rid)
    assert e.value.status_code == 409
    assert _loan_row(lid)["status"] == "active"              # loans.status 未被碰


def test_confirm_races_borrower_return_single_loan_status(env):
    lid = _lend_drill()
    rid = main.open_recall(lid, main.RecallIn(effect="settle"))["recall_id"]
    results = {}

    def do_confirm():
        try:
            main.confirm_recall(rid); results["confirm"] = "ok"
        except HTTPException as e:
            results["confirm"] = e.status_code

    def do_return():
        try:
            main.return_loan(lid); results["return"] = "ok"
        except HTTPException as e:
            results["return"] = e.status_code

    ts = [threading.Thread(target=do_confirm), threading.Thread(target=do_return)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert list(results.values()).count("ok") == 1           # 只允许一种 loans.status 落库
    assert _loan_row(lid)["status"] == "returned"
    assert _recall_row(rid)["status"] == "done"              # 无论谁赢，工单都了结


def test_overdue_scan_follows_recall_effect(env):
    b0 = main.board()
    assert any(l["id"] == SEED_LOAN_ID for l in b0["overdue"])  # 种子在借已逾期
    rid = main.open_recall(SEED_LOAN_ID, main.RecallIn(effect="nudge"))["recall_id"]
    b1 = main.board()
    od = next(l for l in b1["overdue"] if l["id"] == SEED_LOAN_ID)
    assert od["recall_id"] == rid                             # 逾期扫带上工单
    assert b1["counts"]["recalls"] == 1
    main.confirm_recall(rid)                                  # 只催：仍在逾期扫里
    assert any(l["id"] == SEED_LOAN_ID for l in main.board()["overdue"])
    rid2 = main.open_recall(SEED_LOAN_ID, main.RecallIn(effect="settle"))["recall_id"]
    main.confirm_recall(rid2)                                 # 当场结还：退出逾期扫
    assert all(l["id"] != SEED_LOAN_ID for l in main.board()["overdue"])


def test_board_columns_and_recall_list_agree_on_same_loan(env):
    lid = _lend_drill()
    rid = main.open_recall(lid, main.RecallIn(effect="settle"))["recall_id"]
    b = main.board()
    loan = next(l for l in b["active"] if l["id"] == lid)
    rec = next(r for r in b["recalls"] if r["id"] == rid)
    assert rec["loan_id"] == loan["id"]
    assert loan["recall_id"] == rec["id"] == rid
    assert rec["title"] == loan["title"] == "电钻"
    assert rec["borrower"] == loan["borrower"]
    listed = _recall_row(rid)                                 # /api/recalls 与看板同一笔
    assert listed["loan_id"] == lid and listed["title"] == "电钻"
