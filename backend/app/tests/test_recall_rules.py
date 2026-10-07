from app.engines.borrow_rules import (
    check_recall_effect, can_initiate_recall, can_resolve_recall,
)

def test_effect():
    assert check_recall_effect("close_return")["ok"]
    assert check_recall_effect("remind")["ok"]
    assert check_recall_effect("")["reason"] == "bad_effect"
    assert check_recall_effect("whatever")["reason"] == "bad_effect"

def test_initiate():
    assert can_initiate_recall("active", 0)["ok"]
    assert can_initiate_recall("returned", 0)["reason"] == "loan_not_active"
    assert can_initiate_recall("active", 1)["reason"] == "recall_already_open"

def test_resolve():
    assert can_resolve_recall("open")["ok"]
    assert can_resolve_recall("done")["reason"] == "recall_not_open"
    assert can_resolve_recall("cancelled")["reason"] == "recall_not_open"
