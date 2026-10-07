# Borrowboard · 邻里借用

上架 → 借出通过 → 归还；单物件同时仅一笔在借，含逾期判定。

物主收回工单：对在借物发起收回（`POST /api/loans/{id}/recalls`），在借行保持
active、物品不回可借栏，同一在借仅一张未完成工单。效果二选一写进发起与确认：
`close_return` 当场结还并回可借栏，`remind` 只催等借用人自还。确认 / 撤回 /
借用人归还撞车时仅一种 `loans.status` 结果（守卫式更新，败方 409）。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5400 |
| API | 10400 |

0-1：`deposit` / `damage_note` / `neighbor_rating`。
