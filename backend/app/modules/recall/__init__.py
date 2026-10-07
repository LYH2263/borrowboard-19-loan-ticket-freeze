"""物主收回工单（recall work order）。

物主对仍在借的物件发起收回工单；工单效果（effect）在发起时选定、确认时执行：

- ``settle`` 当场结还：确认时把该笔在借结掉（loans.status -> returned），
  物件回到可借栏；
- ``nudge`` 只催待还：确认只了结对借用人的催还提醒，在借保持 active、
  物件仍不在可借栏，等待借用人走正常归还流程自还。

约束：同一笔在借同一时刻最多挂一张未完成（open）工单；借用人自还会把
该在借上的未完成工单一并了结为 done。工单本身从不改写 loans.due_date。
"""

EFFECTS = ("settle", "nudge")

OPEN, DONE, CANCELLED = "open", "done", "cancelled"


def can_open_recall(loan_status: str, open_recalls: int) -> dict:
    """发起前置校验：在借必须 active，且没有未完成工单。"""
    if loan_status != "active":
        return {"ok": False, "reason": "loan_not_active"}
    if open_recalls > 0:
        return {"ok": False, "reason": "recall_already_open"}
    return {"ok": True, "reason": ""}


def confirm_plan(effect: str) -> dict:
    """确认时工单效果对在借/物件的作用。"""
    if effect == "settle":
        return {"close_loan": True, "item_status": "available"}
    return {"close_loan": False, "item_status": None}
