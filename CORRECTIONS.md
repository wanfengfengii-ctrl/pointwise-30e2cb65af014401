# 来源观测更正与分支合流

本次增加两项关联能力：A 发布带旧像校验的来源行更正、撤回和幂等回执；B 让分支合并及固定快照读取消费同一更正状态。共同结果是保留互不冲突的修正，拒绝真实冲突，并能复核历史成果。现有功能、类型、字段身份、异常和串行调用边界见 README.md；本文件扩展其“数据为批次并集”的行为，其余原契约保持。没有更正的输入仍须得到原结果。

## 入口与公开数据

在现有 Lake 上增加两个 Python 方法，参数及结果均可用 JSON 表示。本次不要求增加 CLI 命令；原 JSONL 命令保持兼容。

* read_rows(snapshot) 返回 [{"source": [batch, index], "row": 完整公开行}, ...]。batch 是 append 回执的批次整数编号，index 是原批内从 0 开始的行号，整数不含 bool。地址在分叉、模式修改、更正、撤回和合并后不变，不能按过滤后的行序重新编号。仅返回未撤回行，沿用批次编号、原批内行序；重复内容的不同来源仍分别保留。snapshot 的校验和异常与 read 相同。返回值由调用者持有，修改它不能影响湖表。
* 原 read(snapshot) 的返回格式不变，其结果必须等于 read_rows(snapshot) 各项 row 组成的列表。
* correct(branch, request, changes) 返回 {"snapshot": 整数快照, "request": 原请求串}。request 沿用 append 的非空、至多 128 字符要求。changes 为非空数组，同一来源至多一项；每项键集合须恰好为下述一种格式：
  * {"source": [batch, index], "op": "patch", "before": 路径映射, "after": 路径映射}
  * {"source": [batch, index], "op": "remove", "before": 完整公开行}

patch 的 before、after 是键集合相同的非空对象，键为调用时模式存在的标量路径，结构体子字段使用点路径，例如 meta.quality。不能直接 patch struct 容器。两个映射中的值均按现有字段类型校验；before 匹配批前有效值，after 是新值。NULL 是实际值，未列单元保持不变。remove 的 before 必须包含批前公开行的全部字段及结构体容器，形状、路径及值类型须合法，并等于该行；没有额外 after。标量按类型校验后比较值，float 字段的数值 1 与 1.0 相等；对象比较不受键顺序影响。

source 必须是长度为 2 的数组，batch 为正整数、index 为非负整数，两者均排除 bool。所有旧像都针对调用前同一分支头校验。来源不存在、不属于该分支、已经撤回或旧像不符，抛 ConflictError；非法参数、重复来源、路径、形状或类型抛 ValidationError；未知分支仍抛 NotFoundError。一个调用同时有多类错误时，不要求固定报错优先级，但不得发布任何部分修改。撤回行不允许再 correct；没有显式恢复接口。

correct 请求按分支隔离，且独立于 append 的请求命名空间。内容判等采用现有 catalog.encode(changes) 的规范 JSON 规则，数组顺序有意义、对象键顺序无意义。相同请求同内容重放返回原回执且不移动头，必须在当前模式绑定及来源、旧像校验之前识别，故改名、撤回或后续合并后重放仍成功；异内容重用抛 ConflictError。fork 不复制另一分支的请求命名空间。

更正状态、回执和分支头须在同一现有事务内发布，失败全部回滚。两种 publish_hook 阶段继续生效，钩子及 SQLite/I/O 等非业务异常原样传播，失败请求可重试。原始批次载荷和已发布快照不能被修改；关闭重开后更正、撤回及回执仍保真。append、alter、fork 和后续 merge 必须保留所继承的有效更正状态。全为等值 patch 时可发布等值快照，也可复用原快照；成功回执和后续重放规则不变。

## 数据与模式的交接

更正按来源和字段创建身份保留，改名不换值，删除字段不抹掉其历史更正，同名重建不继承旧身份更正。字段创建身份、固定类型和默认值使用现有能力。没有载荷或覆盖值的单元按该身份的固定默认值解释，显式 NULL 不能当作没有覆盖；把值改回默认值不算相对祖先的有效修改。模式移除字段只影响公开投影，不等于删除该身份的内部数据。

merge 先遵守原有三方模式规则，同时对来源行做三方数据合并，相对共同祖先比较有效状态，而非比较更正请求或事件数量：

* 祖先已有的存活来源：每个标量身份仅一侧改值采用该侧；两侧改为相同值接受；不同单元修改合流；同单元改为不同有效值抛 ConflictError。
* 一侧撤回、另一侧相对祖先所有单元有效值不变，采用撤回；双方撤回接受；撤回对任何单元有效修改抛 ConflictError。即使改过的身份已从当前模式删除，也参与此裁决。祖先已撤回的来源保持撤回。
* 祖先尚无的来源由拥有它的一侧引入，连同该侧的更正或撤回状态；双方共有来源只保留一份。来源归属仍以批次集合为基础，撤回不能抹去批次归属。
* 数据裁决保留双方相关身份，再按合并模式投影。比较范围包括祖先和两侧模式、原批次单元及保留更正所涉及的标量身份；任一侧没有某身份载荷或覆盖时用该身份固定默认值。不能因当前投影不含某字段而丢掉其值或把它当作改 NULL。

多重最近共同祖先沿用现有按快照序递归合成虚拟祖先的规则，同时合成数据状态，任一步数据或模式冲突均拒绝整个 merge。具体顺序与 merge.Ancestry.base 一致：共同祖先中去掉被其他共同祖先继承的节点，剩余节点按快照编号升序；从第一个起累积，将下一个节点与累计虚拟状态合成，基线是累计祖先集合与该节点祖先集合递归得到的虚拟状态，随后并入其祖先集合。数据与模式使用同一个候选顺序和祖先集合，虚拟状态不移动分支、不写请求回执。即使最终两头行值相同，也不能跳过虚拟祖先的冲突检查。合并失败保全目标头、状态、回执及历史快照，source 头始终不变。

允许全量扫描、全量重算和快照状态重写。不要求增量索引、并发写入、自动解决冲突、对象回收、恢复撤回行或外部导出；内部持久表示自行选择，不要求数据库表名或结构。

## 可运行的普通夹具与最小样例

correction_fixture.py 只使用原有 API。运行 python3.11 -B correction_fixture.py 可核对初始两行、地址及现有批次并集；seed(lake) 接收空湖并返回 initial、origin、source、other_source，建立 left/right 分支。它没有实现更正、数据合并或新增读取。以下示例均从一个新 seed 开始，第二行始终保持 {"temperature": 4, "quality": 7}，地址不变。

完成新接口后的联合样例：

~~~python
from branchlake import Lake
from correction_fixture import seed

with Lake() as lake:
    refs = seed(lake)
    src = refs['source']
    left = lake.correct('left', 'temperature-fix', [{
        'source': src, 'op': 'patch',
        'before': {'temperature': 10}, 'after': {'temperature': None},
    }])
    right = lake.correct('right', 'quality-fix', [{
        'source': src, 'op': 'patch',
        'before': {'quality': 1}, 'after': {'quality': 2},
    }])
    merged = lake.merge('left', 'right')['snapshot']
    assert lake.read_rows(merged) == [
        {'source': src, 'row': {'temperature': None, 'quality': 2}},
        {'source': refs['other_source'],
         'row': {'temperature': 4, 'quality': 7}},
    ]
    assert lake.read(refs['origin']) == [
        {'temperature': 10, 'quality': 1},
        {'temperature': 4, 'quality': 7},
    ]
~~~

另从新 seed 分支，两侧分别将第一行 temperature 从 10 改为 11、12，merge 须拒绝。若左侧 remove，before 为 {"temperature":10,"quality":1}，右侧把 quality 从 1 改为 2，merge 也须拒绝；右侧完全未修改时可合流撤回，仅返回原第二行，仍为 other_source。一个 correct 同时 patch 第一行及用错误旧像 remove 第二行，整次拒绝，两个来源均保留原值且请求不占用。

隐藏身份样例：左侧把第一行 temperature 从 10 改 NULL，再 drop temperature；右侧从 origin 撤回第一行。merge 须因隐藏温度的有效修改与撤回相撞而拒绝，不能先投影只比较 quality。另从新 seed 将 temperature 改 NULL、rename 为 old_temperature，再 add 新 temperature（int，default=20），应读 old_temperature=NULL、temperature=20；原 origin 仍读 10。

## 冻结的交叉合并样例

以下表格使用 seed 的第一行，记状态为 (temperature,quality)，第二行及模式均不改。O=origin。A/B/C 等为表内保存的快照变量，不是固定数值编号；调用按表序执行。patch 均使用 fresh request、src=refs['source'] 和表中给出的旧值、新值。

### 两个兼容祖先

|事件|操作|固定结果|
|---|---|---|
|A|left patch temperature:10→11|A=(11,1)|
|B|right patch quality:1→2|B=(10,2)|
|保存|fork(A,'saved_a')；fork(B,'saved_b')|保存两头|
|C|merge('left','saved_b')|C=(11,2)，父 A/B|
|D|merge('right','saved_a')|D=(11,2)，父 B/A|
|E|left patch temperature:11→12|E=(12,2)，父 C|
|F|right patch quality:2→3|F=(11,3)，父 D|
|G|merge('left','right')|G=(12,3)|

G 的最近共同祖先恰为 A、B，虚拟基线为 (11,2)。只选 A 会把 quality 的 2/3 误判冲突；只选 B 会把 temperature 的 12/11 误判冲突。互换最终合并方向也须得到同一行结果，全部旧快照保持表内状态。

### 相同最终行不能掩盖祖先冲突

|事件|操作|固定结果|
|---|---|---|
|A|left patch temperature:10→11|A=(11,1)|
|B|right patch temperature:10→12|B=(12,1)|
|保存|fork(A,'saved_a')；fork(B,'saved_b')|保存两头|
|L|left patch temperature:11→12|L=(12,1)，父 A|
|C|merge('left','saved_b')|C=(12,1)，父 L/B；两侧同改 12|
|R|right patch temperature:12→11|R=(11,1)，父 B|
|D|merge('right','saved_a')|D=(11,1)，父 R/A；两侧同改 11|
|E|right patch temperature:11→12|E=(12,1)，父 D|
|尝试|merge('left','right')|ConflictError，left 仍为 C，right 仍为 E|

最后两头都是 (12,1)，最近共同祖先仍恰为 A、B；从 O 合成它们时 11 与 12 冲突，因此拒绝，不能任选祖先或以两头相等提前接受。

## 交付与验证

保留全部原测试字节及原接口。交付两项联动实现、简短的状态交接不变量说明，并用独立小例状态参照核验更正、读取和合并；参照不得调用生产数据裁决器来生成期望。覆盖上述联合样例、两种交叉合并、NULL/默认值/字段身份、请求重放、批尾拒绝、两个发布阶段异常及关闭重开。无需证明理论最优性或压测性能。

沿已有 .pointwise-runtime.json 更新 verify，使其执行完整原回归和新增业务核验；demo 须展示实际更正合流、冲突拒绝及保留的历史行，不能只打印成功。保留 version=1、kind=cli，setup 仍为准备命令 argv 的列表，verify/demo 各为一个 argv 数组。可复用 demo.py 或另设业务演示入口；不要求固定新增测试文件名、测试数量或内部表结构。
