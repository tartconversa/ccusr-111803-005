# 会员优惠

仓储会员资料的本地演练来源与证据化采集。目录、适用范围、规则正文是三个可独立改变的观察来源；合成资料位于 `fixtures/source.json`。

## 为什么这样设计

只按活动编号保存最新一份资料时，后来的规则文本会静默覆盖早先的对外解释，门店客服无法核对"目录写的"和"结算说明改的"是否还是同一次发布。本项目把每个来源都变成可互相核验的观察，并把每次规则改变作为证据长期保留：

- **三类独立观察**（`app/domain.py`）：目录（面向谁、什么优惠）、适用范围（哪些门店、哪些会员等级）、规则正文（对外解释原文）。每类观察带稳定标识、发布期与内容指纹（SHA-256），任何一类空白或缺失都不能由另外两类补齐。
- **同次发布核验**（`app/policy.py`）：同一活动同一 edition 的三类观察组成一次发布，交叉核验身份、会员等级、门店、优惠类型是否互相一致；不同发布期或不同范围的条件拒绝合并。
- **追加式证据链**（`app/records.py`）：观察收据只增不改，规则改变产生新收据而不是覆盖；每条收据携带内容指纹与前一条指纹形成哈希链，重启后可验证是否被篡改。
- **三态优惠判定**：`available`（同一次有效发布且门店、等级都在范围内）、`unavailable`（有效发布明确排除，有据可查）、`pending`（观察缺失、页面空白、来源故障、同次发布矛盾）。**未知永远是 pending，绝不写成"无优惠"。**
- **答复留痕与追查**（`app/service.py`）：客服每次答复记录结论与依据的收据编号；重启后可用 `trace` 调出旧答复的全部原始证据，并列出哪一类会员仍待重新核实（附最近一次有效发布线索）。

## 运行

- `python -m unittest discover -s tests -v`：运行本地来源与证据库回归（含真实 Chromium 浏览器测试）。
- `python -m compileall -q app tests`：检查项目代码。
- `python -m app.site`：在 8765 端口启动演练来源。
- `python -m app.cli`：另一个终端运行端到端演练（两期发布、规则改变、空白页、重启追查）。

单独命令（证据库默认在 `data/evidence.sqlite3`）：

- `python -m app.cli collect [--edition 1|2] [--blank rule:member-b]`：采集整期三类观察并留证、核验发布。
- `python -m app.cli answer <活动标识> <门店> <会员等级> [--edition 2]`：客服核对，给出三态答复并留痕。
- `python -m app.cli pending <活动标识> <门店> 甲 乙 丙`：列出仍待重新核实的会员等级。
- `python -m app.cli trace <答复编号>`：追查旧答复依据的原始观察收据与哈希链完整性。
- `python -m app.cli verify`：校验证据哈希链。

浏览器页面位于 `/catalog.html`、`/scope.html`、`/detail.html`，使用前准备 `requirements.txt` 中的 Playwright 与 Chromium。
