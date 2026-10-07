from app.modules.recall import EFFECTS, can_open_recall, confirm_plan

def test_effects():
    assert set(EFFECTS) == {"settle", "nudge"}

def test_can_open_recall():
    assert can_open_recall("active", 0)["ok"]
    assert can_open_recall("returned", 0)["reason"] == "loan_not_active"
    assert can_open_recall("active", 1)["reason"] == "recall_already_open"

def test_confirm_plan():
    assert confirm_plan("settle") == {"close_loan": True, "item_status": "available"}
    assert confirm_plan("nudge") == {"close_loan": False, "item_status": None}
