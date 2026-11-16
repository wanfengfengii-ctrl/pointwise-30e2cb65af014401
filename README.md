# Branchlake

Python 3.11 标准库实现的离线研究观测湖表。一个 SQLite 文件保存一个表：研究员从固定快照分叉，补列、改名、删除字段，追加观测批次，再合并不冲突的分支。原始批次、字段创建记录和已发布快照不原地修改。

## 运行

无需安装依赖，在项目根目录执行：

```sh
python3.11 -B -m unittest discover -s tests -t . -v
python3.11 -B demo.py
python3.11 -B -m branchlake --db research.sqlite < examples/session.jsonl
```

demo 和测试仅在各自 TemporaryDirectory 内创建数据库；CLI 写入显式指定的数据库。重复运行 session 请使用新数据库。运行清单 verify 执行完整原套件。

## Python API

```python
from branchlake import Lake

with Lake(':memory:') as lake:
    initial = lake.create([{'path': 'sample', 'type': 'int'}])['snapshot']
    lake.fork(initial, 'review')
    lake.alter('review', [
        {'op': 'add', 'path': 'quality', 'type': 'int', 'default': 7}
    ])
    receipt = lake.append('review', 'batch-1', [{'sample': 1, 'quality': None}])
    published = lake.merge('main', 'review')['snapshot']
    assert lake.read(published) == [{'sample': 1, 'quality': None}]
```

Lake(path=':memory:', publish_hook=None) 管理一个串行调用的连接，可用上下文管理器关闭。多个连接可按顺序交替访问同一文件；不承诺同一实例的多线程使用。构造器创建空目录表，create 负责一次性初始化业务表。head(branch='main') 返回当前快照整数编号；read 只接受明确的正整数快照，不接受分支名，不回退到当前头。

create(fields) 返回 {branch, snapshot}，建立 main 和空快照。fields 为按父字段在前排列的定义数组，每项包含 path、type 和可选 default。fork(snapshot, branch) 返回 {branch, snapshot}；新分支名必须唯一，格式为字母开头的 1–64 位字母、数字、下划线或连字符。

alter(branch, changes) 返回 {snapshot}，逐项应用非空 changes 并整次提交：

* add: {op: 'add', path, type, default?}
* rename: {op: 'rename', path, to}
* drop: {op: 'drop', path}

字段路径是一段或两段标识符，以点连接。支持 int、float、str、bool 及仅根层的 struct；不支持数组和更深结构。先创建 struct 再添加其标量子字段。int 不接受 bool；float 接受有限 JSON 数值，保留整数精度；所有标量都接受 NULL。默认值省略时为 NULL，struct 的默认值只允许 {}。struct 容器自身必须是对象，不能为 NULL；缺省容器视为空对象，再逐子字段应用默认值。删除 struct 连同其子字段删除。改名可在合法结构内移动标量；struct 整体改名保留其子字段关系。

每次 add 产生湖内唯一创建身份。名字、分支、操作顺序、显示路径都不是身份。改名保留身份；删除后同名创建产生新身份，可以采用不同类型。类型和默认值在该身份创建后固定，改变类型需删除重建。一次 alter 中任何中间模式无效则整次拒绝，包括重名、非法父节点及深度。

append(branch, request, rows) 返回 {snapshot, batch, request}。rows 是非空 JSON 对象数组，结构体使用嵌套对象，拒绝未知路径及类型错误。省略的标量字段不保存载荷单元，显式 NULL 保存为 NULL；读取任意缺席单元时使用读取快照中该字段身份创建时固定的默认值。包含新字段创建前的历史行也遵守此规则。不会借用旧同名身份的值。批次全部校验后才写入、发布。相同 branch/request 和相同 JSON 内容（对象键顺序无关）重试返回原回执且不移动头，即使模式已变；相同请求异内容抛 ConflictError。不同分支的请求命名空间独立。没有业务主键去重，重复行保留。

merge(target, source) 返回 {snapshot}，只移动 target。以快照 DAG 的共同祖先做三方模式合并；多重最近共同祖先先按快照序递归合成虚拟祖先，不能无冲突合成则保守拒绝本次合并。共同身份仅一边改变时采用该边，两边相同改变可接受；不同改名、改名对删除等不兼容修改拒绝。同路径独立新增即使类型和默认值相同也拒绝；父节点删除对另一侧新增子字段拒绝。父 struct 改名与另一侧补子字段可合并。同一祖先字段一侧改名、另一侧删除重建属于冲突。两个不同祖先字段分别发生这些演进可以合流。

合并数据为双方不可变批次的集合并集，不重复共同继承批次。所有读取按全湖批次创建编号升序、批次内输入行序输出，合并方向不改变行序和多重集。相同分支自合并、已合并分支重复合并可产生新快照，但不重复行。

read(snapshot) 返回该固定模式下的嵌套 JSON 行数组，字段全部出现。返回对象由调用者持有，修改它们不影响持久化数据。字段创建、批次、回执、快照及目标分支头在 SQLite 单次事务内发布；失败回滚，历史结果不变。publish_hook(stage, snapshot) 是可选的发布观察入口，stage 为 snapshot_inserted 或 head_updated，在提交前调用；抛出的异常原样传播并回滚。钩子不得重入写入，用于嵌入方发布观察及回滚检查。

read_rows(snapshot) 返回 `[{source: [batch, index], row: 完整公开行}, ...]`，地址稳定、只含未撤回行；read 恒等于其 row 列表。correct(branch, request, changes) 对指定来源做带旧像校验的 patch 或 remove，返回 `{snapshot, request}`。更正按来源地址与字段创建身份保存，随分支继承并在 merge 中按相对共同祖先的有效状态三方合流。完整规则见 CORRECTIONS.md。

ValidationError 表示格式、字段、值或参数非法；NotFoundError 表示未知快照或分支；ConflictError 表示重复初始化、分支重名、请求冲突或不可合并模式。SQLite/I/O/钩子等非业务异常原样传播。失败不产生可见的部分业务成果。

## JSON CLI

每行一个完整 JSON 命令，每行输出一个 {ok: result} 或 {error: class, message}。op 为 create/fork/head/alter/append/merge/read，其余键与 Python API 的参数同名且必须恰好匹配。head 命令也须明确 branch。业务错误后继续处理后续行，本次进程若有任何业务/JSON 错误退出 2，否则 0。系统错误传播，不伪装为业务错误。

examples/session.jsonl 是有限正常输入；demo 展示两支演进、NULL、同名重建、冲突拒绝以及关闭重开后历史不变。测试分别核对字段校验、幂等请求、非法批次尾部原子拒绝、模式变更回滚、双向与交叉分支合并、固定历史读取和事务发布失败。

## 实现职责

branchlake/schema.py 管理字段创建身份、层级、默认值及模式演进；ingest.py 校验并保存稀疏身份载荷及回执；catalog.py 持有 SQLite 目录、分支头和事务；corrections.py 校验并保存来源更正/撤回、按快照投影行；merge.py 裁决共同祖先及模式与更正数据合流；reader.py 按固定快照输出字段。它们经 Lake 的公开方法形成完整读写闭环。批次整值编码和解码，读取整值物化；不含查询优化、导出格式转换、分布式协调或对象回收。
