# 怎么验证这次改动

所有改动都在副本 `F:\AzurLaneAutoScript-dev`（当前分支 `island-improved`），
正式目录 `F:\AzurLaneAutoScript` 没有被修改。下面按"不用模拟器 → 要模拟器"的顺序排列。

---

## 0. 先把副本的界面启动起来

双击副本根目录的 **`verify_gui.bat`**，等几秒，浏览器打开：

**http://127.0.0.1:22268**

（端口 22268 和正式目录的 22267 分开了，两边能同时开。关掉那个黑窗口就停服务。）

> 注意：副本的 GUI 里点"启动"会连模拟器。你的正式 Alas 也在跑，两个会抢同一个模拟器。
> 只想看界面的话，不要在副本里点启动；要完整跑任务，先把正式的停掉。

---

## 1. 界面验证（3 分钟，不需要模拟器）

打开 http://127.0.0.1:22268 后：

### 1.1 物品选择控件（①）

左下 **岛屿 → 岛屿生产**，看四个物品字段：

| 字段 | alas.json 里实际存在 | 界面上应该看到 |
|---|---|---|
| 每日缓冲物品 (DailyBufferItems) | 78 项 | 78 行 |
| 硬性下限物品 (HardFloorItems) | 0 项 | 只有一个空的添加行 |
| 闲置累计物品 (IdleAccumulatingItems) | 20 项 | 20 行 |

每行长这样：

```
[物品下拉 ▾]  [数量输入框]  [编辑] [删除]
[物品下拉 ▾]  [数量输入框]  [添加]          ← 最后一行是新增用
```

检查四件事：

1. **文字是横排的**（之前一坨是因为被 `1fr 13rem` 的两列网格挤成竖排）
2. **三个按钮高度一致**，不再是一高一矮
3. **行距紧凑**（之前 `put_text` 渲染成 `<p>` 带了 16px 下边距）
4. **数量右对齐，且贴着输入框右边**，不是飘到很远

### 1.2 改动是否真的写进配置（这是关键）

界面上随手改一个：点某行 `[删除]`，或者选个物品 + 填数量 + 点 `[添加]`。
然后打开 `F:\AzurLaneAutoScript-dev\config\alas.json`，搜 `DailyBufferItems`：

```json
"DailyBufferItems": "小麦 (2000): 300.0\n玉米 (2001): 500.0\n..."
```

格式没变，还是 `名称 (id): 数量` 一行一个 —— 也就是说**存储格式完全兼容**，
老配置能读、新配置别的工具也能读。

### 1.3 店员锁开关（③）

**岛屿 → 岛屿经营**，往下翻，能找到 **"锁定店员"** 复选框，默认勾上，
说明文字是新的口径（"两店都在经营、且她下班后还排得下一轮才等；跨 00:00 一律等"）。

它在 `alas.json` 里的键是 `IslandRestaurant.IslandRestaurant.WaitressLock`。
取消勾选保存后这个键会变成 `false`。

### 1.4 货运委托任务（⑤）—— 已实机验证通过

左侧任务列表里应该出现 **IslandTransport**（货运委托），
进去只有一个 `启用` 开关，默认**关闭**。

2026-10-23 13:53 实机跑通，日志证据：

```
Transport commissions chosen: P2, 05, gain 399.3 PT over 12h
Transport plan: swap [('P3', '05')], submit ['P2', 'P3']
[TRANSPORT_TIMER_OCR] ['01:07:15', '01:05:43']
Transport next run time: 2026-10-23 15:00:57
```

一次完整的「领奖 → 扫槽 → 选委托 → 换槽 → 提交 → 排下次」全部走通。

---

## 2. 离线自检（不用模拟器，随时可重跑）

在副本目录执行这三条：

```bash
cd F:\AzurLaneAutoScript-dev

# ① 物品控件：加/删/改/清空 + 非法输入拒绝
./toolkit/python.exe dev_tools/island_widget_check.py

# ③ 店员锁：9 个时间场景
./toolkit/python.exe dev_tools/island_waitress_lock_check.py

# ① 用真实 alas.json 渲染，核对行数
./toolkit/python.exe dev_tools/island_render_check.py
```

我刚跑过的结果：

- `island_widget_check.py` → 6 个回调、非法物品/负数/0 全部被拒绝、清空写入 `{}`
- `island_waitress_lock_check.py` → **9/9 checks passed**
  - 14:00 下班 + 8h 来得及 → 锁
  - 18:00 下班 + 8h 跨过 00:00 → 不锁，换人
  - 16:00 下班刚好卡在 00:00 → 锁
  - 跨日 01:00 → 锁
  - 对方店休息 / 未开场 → 判为 `idle`，正常选她
  - 状态读不到 → `unknown`，保守换人
- `island_render_check.py` → 78 / 0 / 20 项，`<p>` 标签 0 个，往返解析一致
- 货运委托测试 → **83 passed**

---

## 3. 实机验证（需要模拟器 + 游戏）

先停掉正式 Alas，在副本里点启动，然后看 `F:\AzurLaneAutoScript-dev\log\` 下最新的日志。

### ② 生产次序 + 在产数量

搜 `Calculated recipe sequence to run`，这一行会列出本轮的配方顺序。
期望看到的是：**先满足每日需求的批次，后面才是闲置累计**（`idle accumulating rate` 那批排在最后）。

再看每条 `Recipe ... stock: N`：

- `N` = 仓库扫到的数量 + **已经在生产中的数量**
- 想确证在产有没有算进去，找 `total committed stock: N` 这行 —— 每派一批生产，
  这个数就会累加，下一轮计算的 `stock` 也会跟着变大

对比方法：如果某个物品仓库里有 100、正在生产 100，那么判断"要不要再生产"时
应该按 200 来算。旧版只按 100 算，会多产一批。

### ③ 店员锁

搜 `is locked by`：

```
Waitress 欧根亲王 is locked by 603 啾啾简餐 until 2026-10-23 16:00:00,
delay restaurant 604 乌鱼烤肉 for her instead of replacing her
```

然后这条餐厅会被排到 `她下班 +1 分钟`：

```
Restaurant 604 乌鱼烤肉 waits for waitress 欧根亲王 until ...,
will start this restaurant when she is free
```

如果时间来不及，日志会换个说法（`too late to fit another shift before ...`），
然后正常换人 —— 这就是你要的"两次都要经营才锁"。

### ④ 采集优先 A 适应

搜 `Collect candidates for workslot`：

```
Collect candidates for workslot 1: 企业(S), 信浓(A), 拉菲(B)
```

列表按 S → A → B 排序，实际选的是第一个。旧版是随机/首元素，
现在只要场上有 S 或 A 就一定选它们。

### ⑤ 货运委托 —— 已验证可用（2026-10-23 13:53 实机）

打开 IslandTransport 的启用开关，跑一轮，日志里会出现
`transport` 相关的扫描与选择记录。首次运行前建议先让 `IslandProductionPlanner`
完整跑过一轮，否则它会读不到技术状态直接跳过（不报错，就是不接委托）。

已知小问题（不影响提交，只影响收益估算）：日志里会出现
`Unknown transport reward item in list_reward is left out`。
委托一览里认不出的奖励图标会被静默丢弃，导致该委托的收益被低估，
MILP 选委托时可能略微次优。当前模板库有 35 种物品，
**缺「皮料」**（羊毛 Wool 有，皮革没有）——正是上游 PR 还是 draft 的原因。

---

## 4. 出问题怎么回报

把 `F:\AzurLaneAutoScript-dev\log\` 下最新那份日志发我，或者告诉我：

- 界面上哪个字段长什么样（截图最直接）
- `config/alas.json` 里对应字段被写成了什么
- 日志里最后 20 行

---

## 5. 想切回原版

```bash
cd F:\AzurLaneAutoScript-dev
./toolkit/Git/mingw64/bin/git.exe checkout master            # 原版
./toolkit/Git/mingw64/bin/git.exe checkout island-improved   # 改动版（含货运委托）
```

两个分支都在本地，没有推送过任何远程仓库。
