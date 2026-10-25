# 货运委托"认不出物品"的原因和补法

## 状态：皮料已补（2026-10-23）

用你给的仓库截图做出了模板，已落在 `assets/island/transport/Pelt_2.png`：

| 项目 | 结果 |
|---|---|
| 来源 | 仓库截图 116x91，图标格 69x69 |
| 处理 | 去紫色稀有度边框、抹掉"3701"数量角标、抹掉左下小水印、缩放到 46x46 |
| 物品映射 | `Pelt` → **2604**（皮料） |
| 四视图模板尺寸 | primary_submission 48x48 / list_submission 38x38 / primary_reward 38x38 / list_reward 33x33 |
| 端到端匹配 | 把模板按 0.715 贴进 71x60 奖励卡，matcher 返回 **2604** |
| 与其它 32 个模板的最高相似度 | **0.282**（Coffee Beans），远低于 0.70 阈值，不会误抢 |

对比图见 `dev_tools/preview/pelt_vs_existing.png`（Wool / Cotton / Milk / Pelt 并排放大）。

**需要重启 Alas 才生效**（模板是 `cached_property`）。
下次跑货运委托时留意日志：原来那两条 `Unknown transport reward item in list_reward`
应该消失，并且 `Transport Px: [...] -> [...]` 里会出现 `Pelt x N`。

> 一个诚实的保留：这张模板来自**仓库界面**，前提是仓库格子和委托卡里的图标用的是同一套美术资源、同一显示比例。
> 同图其它物品都能认出来说明这套比例体系是自洽的，但皮料本身没验证过。
> 如果重启后仍然认不出，说明两边比例不一致，那时需要一张**委托一览/委托详情里皮料奖励**的截图，我按真实比例重做。

---

## 为什么会漏

不是坐标或缩放算错了——同一张截图里二十多个物品都认对了，只有个别漏掉。
真正的原因是**模板库里没有那个物品的图片**。

匹配流程（`module/island/transport.py`）：

1. 模板全部来自 `assets/island/transport/` 目录的 png
2. 文件名必须是物品的**英文名**，空格换下划线 —— `TransportItemMatcher.item_ids`
   用 `DIC_ISLAND_ITEM[...]['name']['en'].replace(' ', '_')` 建映射
3. 匹配时把模板按视图缩放到实测比例（`list_reward` 是 0.715 / 0.665），
   在卡片区域里滑动比对，相似度过 0.70 且颜色差小于 20 才算命中
4. 没有模板 → `item_id is None` → 这个奖励直接丢弃

我比对了模板库和物品字典：

| 物品 | 英文名（文件名） | 模板 |
|---|---|---|
| 皮料 | `Pelt` | **缺** |
| 新鲜蜂蜜 | `Fresh_Honey` | **缺** |
| 羊毛 | `Wool` | 有 |
| 橡胶 | `Rubber` | 有 |

模板库现有 35 种物品，皮料是唯一已确认会出现在委托奖励里的缺口
（你日志里那 2 条 `Unknown transport reward item` 就是它，可能还夹着一个别的）。

**影响**：只是那条委托的收益被低估，MILP 选委托时可能略次优。
不影响提交，不影响实际到账收益。

---

## 怎么补

### 第一步：让程序把认不出的图标存下来（已改好）

我在 `read_rewards()` 里加了 `record_unknown_reward()`：以后遇到认不出的奖励卡，
会把它裁下来存到：

```
assets/island/transport/new_templates/reward_list_reward_s0.665_<时间戳>.png
```

文件名里的 `s0.665` 是当时的缩放比例，后面换算尺寸要用到。

> 这一步需要重启 Alas 才会加载新代码。重启后跑一次货运委托就会自动存图。

### 第二步：把图转成模板（工具已备好）

```bash
cd F:\AzurLaneAutoScript-dev

# 看看有哪些等着处理
./toolkit/python.exe dev_tools/transport_add_template.py --list

# 转成模板（--name 用英文名，中文名也行，会去字典里查）
./toolkit/python.exe dev_tools/transport_add_template.py \
    --image assets/island/transport/new_templates/reward_list_reward_s0.665_xxx.png \
    --name Pelt
```

工具会做四件事：

1. 去掉白底（和匹配器同一套规则）
2. 按文件名里的缩放比例还原成基准尺寸
3. **自检**：把结果缩回原比例，去原图上做一次匹配，分数低于 0.70 就拒绝写入
4. 写入 `assets/island/transport/Pelt.png`

我最开始已经自测过一遍：随便拿一张 `Eggs.png` 模拟成奖励卡再走一遍流程，
还原出来的尺寸 69x47（原始 69x46），自匹配分数 **0.993**，链路是对的。

### 第三步：重启生效

模板是 `cached_property`，重启 Alas 后重新扫描目录就生效了。

---

## 更快的办法

上面要走"重启 → 跑一轮 → 拿到图 → 转换"四步。如果你不想等，
**直接在游戏里截一张能看到皮料图标的图**发我（仓库、委托详情、生产配方界面都行），
我从截图里裁出来、按基准尺寸存成 `Pelt.png`，一步到位。

需要的截图特征：皮料图标要清晰、完整、没被其他 UI 遮住，最好有白底或纯色底。

---

## 一个容易踩的坑

**不要直接把图丢进 `new_templates/` 就以为生效了。**
匹配器只扫描 `assets/island/transport/*.png` 这一层，`new_templates/` 是暂存区，
故意不参与匹配（避免半成品模板误伤识别）。必须经过第二步转成正式模板。
