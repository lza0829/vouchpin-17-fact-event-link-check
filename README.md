# Vouchpin（券钉簿）

骨架：券事件入湖 → 核销事实钉扎 → 过期/退回窗（后挖）→ 批次互证。

本地：
- API: `cd backend && python -m uvicorn app.main:app --port 10800`
- Web: `cd frontend && npm install && npm run dev`（5800）

## 事实-事件链完整性（全局互斥）

钉扎前逐条校验将钉的 open 事实与事件湖的链接，四条规则：

1. **事件存在**：`event_id` 悬空 / 事件被删 → `event_missing`
2. **事件类型为 redeem** → 否则 `event_type_mismatch`
3. **金额一致**（两位小数比对）→ 否则 `amount_mismatch`
4. **业务日一致** → 否则 `business_day_mismatch`

全局设置 `chain_integrity_policy` 二选一（设置页或 `PUT /api/settings/chain-policy`）：

- `reject`（默认，拒钉）：任一事实断链即整批拒钉（HTTP 422 + 断链清单），
  **不写批、不锁日**，事实保持 open 不被改动。
- `materialize`（以事件为准重物化后钉）：金额/业务日漂移在**同一钉事务**内
  按事件重写事实并写 `fact_rewrite_audit` 审计旧值，重写后重检通过才钉；
  事件缺失/类型不符无法物化，**仍拒钉**；重写或重检失败整笔回滚，
  禁止静默跳过断链行却称钉成功。

### 留痕与复查

- `chain_check_runs`：每次钉扎检查一次运行（策略、结果、断链/重写计数、关联批次）。
- `chain_breaks`：断链清单（事实 id、事件 id、原因、事实/事件快照、是否已物化修复），
  拒钉与物化均落库，可在「钉扎批次」页或接口复查。
- `fact_rewrite_audit`：物化重写的旧值/新值审计。

接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/redeem/preview` | 只读预览断链（不落库），含策略与阻断数 |
| POST | `/api/redeem/seal` | 钉扎；422 响应体即断链清单（含 `run_id`） |
| GET | `/api/chain/breaks` | 全量断链清单复查 |
| GET | `/api/chain/runs` / `/api/chain/runs/{id}` | 检查运行及单次清单/审计 |
| GET | `/api/batches/{id}` | 批明细：事实、检查运行、重写审计 |
| GET/PUT | `/api/settings/chain-policy` | 读取/切换全局互斥策略 |

不变量：**不存在「断链仍落 sealed 且无清单」的状态**——拒钉不产批次；
物化成功的批次其检查运行必带已修复清单与审计；任何失败路径均回滚。

测试：`cd backend && .venv/bin/python -m pytest tests/ -q`
