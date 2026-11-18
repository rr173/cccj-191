# dawgdict — 大词典前缀 / 容错查询器

面向数据清洗场景的标准词查询器：前缀精确匹配 top-k，以及 0–2 次
Levenshtein 编辑的纠错候选 top-k。核心是一个**自实现的最小无环确定
词典自动机（DAFSA）**，词权重与词身份另存，全部查询带可证明安全的
排名上界与提前停止规则。纯 Python 标准库，无第三方依赖。

```
dicttool/           库与 CLI（python -m dicttool ...）
  validate.py       JSONL 词条 / 查询参数校验
  externsort.py     外排：内存额度内攒 run、落盘、堆归并、重复词权重相加
  dawg.py           增量式最小 DAFSA 构建（寄存器法）+ 精确状态权重上界
  packformat.py     单文件包格式常量
  builder.py        构建管线：校验 → 外排 → 最小化 → 写包（原子提交）
  reader.py         只读打开 + 全部结构校验 + 最小性/上界审计
  query.py          prefix / suggest 的 best-first 有界搜索
  api.py            可反复打开的只读 API（Dictionary.open）
  verify.py         独立暴力参考实现（不复用图遍历）
  cli.py            命令行
tests/              unittest 套件（48 个测试）
tools/gen_words.py  规模词表生成器（确定性，不提交生成物）
tools/measure.py    资源测量（wall / maxrss，替代 /usr/bin/time）
```

---

## 1. 最小化语义（我们的选择）与可检验证据

**选择：无权接受器 + 右语言等价最小化。** 自动机只接受词表语言，
不携带权重；两个状态等价当且仅当它们的右语言相同：

```
L(p) = { s : 从 p 出发可接受 s }  ==  L(q)   ⇔   p ~ q
```

对无环自动机，这等价于自底向上的结构签名唯一性：
`sig(p) = (terminal(p), sorted((label, child_id) ...))` 全表唯一。
权重与词身份保存在独立的、按码点有序的二分可查词条表中；
**词的身份由路径上的弧标签序列承担，从不由状态承担**——终态可以被
多个词共享（例如 `ab`/`cb` 在最小自动机里终结于同一状态，甚至当词表
只有这两个词时，"a 之后"与"c 之后"的状态也因右语言相同而合并），
但它们的词条、权重、排名范围完全独立。

**为什么不是"压缩线性链"**：寄存器法（Daciuk et al. 2000 的增量
最小化，本仓库自实现）在构建时对每个冻结状态做全表签名查找，
任何位置的等价后缀子图都会收敛到同一状态，与词的出现位置无关。

**可检验证据**（`python -m dicttool check --dict D --deep`）：

1. *最小性审计*：重建全部状态的签名映射，任何两个不同状态签名相同
   即失败——无环确定自动机上这就是右语言等价的判定；
2. *共享证据*：报告入度 >1 的状态数与最大入度（后缀共享的直接证据）；
3. *上界审计*：从词条表独立重算每个状态的 `max_weight`（沿每条词的
   接受路径逐状态取 max），要求与存储值逐一相等；
4. *逐词审计*：词表中每个词必须被自动机接受且终结于终态。

`python -m dicttool explain --dict D --word ab --word cb` 打印每个词的
状态 id 路径与弧标签序列，可直接看到共享与分叉。

`max_weight` 的语义：**恰好**是"接受路径经过该状态的所有词"的最大
权重（逐路径计算，不是子树传播——共享汇点的权重不会漏进无关分支，
因此上界尽可能紧；它仍是上界，查询证明只依赖"经过 n 的词权重
≤ max_weight[n]"这条不变式）。

## 2. 排名上界与停止规则（安全性论证）

两个查询都是堆式 best-first 搜索，**从不**先枚举全部候选再排序取 k。

### prefix(p, k) — 排名 (weight 降序, word 码点升序)

前沿堆中两类条目共享一个键空间：

- 界条目 `(-max_weight[n], path, 1, nid)`：`path` 是到达该状态的拼写；
- 词条目 `(-weight, word, 0, "")`：精确词。

不变式：每个未发射的词都被某个堆条目"覆盖"——界条目覆盖其子树中
所有词。界条目的键是乐观下界：子树中任何词权重 ≤ `max_weight[n]`，
且拼写**严格大于** `path`（子树所有词共享前缀 `path`，而 `path`
本身若是词，已在展开该界时作为独立词条目入堆）。因此弹出顺序即
排名顺序：词条目被弹出时，剩余界条目的权重上界更小，或上界相等但
拼写下界 ≥ 该词，都不可能再藏有排名更靠前的词。发射第 k 个词即停
（`stopped_by="k"`），或堆空（`stopped_by="exhausted"`）。

### suggest(text, e, k) — 排名 (distance 升序, weight 降序, word 升序)，e ∈ {0,1,2}

前沿条目携带拼写路径的 Levenshtein DP 行 `row`（`row[i]` = 已拼路径
与 `text[:i]` 的最小编辑距离，值截断到 `e+1`）。关键事实：沿任何弧，
`min(row)` 单调不减（每步转移只加非负代价），所以 `min(row)` 是该
条目子树中所有词最终距离的有效下界；`max_weight[n]` 界权重；`path`
界拼写（同上）。键空间：

- 界条目 `(min(row), -max_weight, path, 1)`；
- 词条目 `(distance, -weight, word, 0)`。

与 prefix 同一论证：词条目被弹出时，不存在能藏更优词的未展开界。
`min(row) > e` 的弧被剪枝（`pruned_by_distance` 计数），这是唯一
的剪枝；等权重/等距离且拼写交错时搜索会按要求退化（多做展开），
统计如实报告展开数，不伪装成剪枝。

**公共后缀上的状态共享不混淆词身份**：suggest 的前沿条目携带完整
拼写路径，同一状态经不同路径到达即不同条目；终态被多个词共享时，
每个词各自由其路径产生独立候选。词表验证（`verify`）对全部词独立
计算编辑距离与排序，逐 k 对照图查询结果。

### 内存额度

- 构建：`--mem-budget` 限制内存排序 run，超出落盘到 `--tmp-dir`
  （run 文件），归并后流式进入最小化；词条表全程不在内存驻留。
  （已说明的限制：最小化后的**图**本身在内存构建。）
- 查询：`--max-frontier` 限制前沿堆条目数；超出时抛出
  `FrontierOverflowError`（退出码 4）——**响亮失败，绝不静默丢弃
  候选**。堆条目大小有清晰上界：DP 行 ≤ len(text)+1 个单元
  （len(text) ≤ 4096），路径 ≤ len(text)+2 个码点。
- 词条最大长度：构建期 `--max-word-len`（默认 256 码点，超出的词条
  被拒绝），写入包头，查询期同样强制。

### 确定行为

`k=0` → 立即返回空（`stopped_by="k=0"`）；无结果 → 空表
（`stopped_by="exhausted"`）；空前缀 → 全词典 top-k；空 text 的
suggest → 距离即词长，按排名规则返回；`max_edits` 只接受 0/1/2；
查询串含孤立代理码点 → 校验错误。

## 3. 包格式与完整性

单文件自包含（可复制、不依赖构建目录或常驻对象）：

```
MDAWG001 | u64 header_len | header(JSON) | nodes | arcs | index | entries | MDAWGEND | sha256
```

- `nodes`：20 字节定长记录（arc_start, arc_count, flags, max_weight）；
- `arcs`：8 字节（码点 label, target）；
- `index`：word_count+1 个 u64 偏移；`entries`：变长
  （u32 字节长 + UTF-8 + u64 权重），按码点序（== UTF-8 字节序），
  权重经二分查找读取（计入 `entry_probes` 统计）；
- 尾部提交标记 + 全文 sha256；构建先写临时文件再原子改名。

打开即全量校验（任何一项失败即拒绝）：尺寸与声明布局（截断）、提交
尾标与全文哈希（未完成构建/位腐败）、各区段 sha256、弧目标越界、
弧标签非法（代理码点/>U+10FFFF）或未排序、词条表严格递增/合法
UTF-8/长度上限、**全图三色 DFS 判环**（含不可达状态）。
`check --deep` 另做最小性与 `max_weight` 审计。打开代价为 O(包大小)，
17 万词/12.7 万状态的包实测约 4.3s（见下）。

## 4. 命令

```bash
# 构建（源词表 -> 自包含包；外排预算 32MiB，临时文件入 /tmp）
python3 -m dicttool build --src words.jsonl --out dict.mdawg \
    --mem-budget 33554432 --tmp-dir /tmp

# 重新开包查询（每次调用都是全新打开，无驻留状态）
python3 -m dicttool prefix  --dict dict.mdawg --prefix "data_" --k 10 --stats
python3 -m dicttool suggest --dict dict.mdawg --text "cleantion" \
    --max-edits 2 --k 10 --stats

# 导出规范词条并与期望差分（dump 输出已按码点排序）
python3 -m dicttool dump --dict dict.mdawg --out /tmp/canon.jsonl
diff -u expected.sorted.jsonl /tmp/canon.jsonl

# 完整性 / 最小性 / 上界审计；共享证据
python3 -m dicttool check --dict dict.mdawg --deep
python3 -m dicttool explain --dict dict.mdawg --word ab --word cb

# 独立验证：从规范词条表暴力计算全部距离与排序，逐查询对照
python3 -m dicttool verify --dict dict.mdawg --text "cleantion" --max-edits 2 --k 10
python3 -m dicttool verify --dict dict.mdawg --random-queries 20 --k 8 --seed 42

# 只读 API（可反复打开）
python3 - <<'PY'
from dicttool.api import Dictionary
with Dictionary.open("dict.mdawg") as d:
    print(d.prefix("ab", 5))
    stats = {}
    print(d.suggest("ab", 2, 5, stats=stats), stats)
PY
```

查询统计（`--stats`，stderr）字段：`expansions`（界展开数）、
`arcs_scanned`、`word_candidates`、`weight_lookups`（词条表二分次数）、
`entry_probes`（二分步数）、`heap_pops`、`frontier_max`、
`pruned_by_distance`（suggest 剪枝）、`stopped_by`（k / exhausted /
k=0）。全部为实测计数。

## 5. 规模复现与资源测量

```bash
# 生成 100 万行词表（确定性；生成物勿提交，.gitignore 已排除）
python3 tools/gen_words.py --n 1000000 --seed 7 --out /tmp/words.jsonl

# 构建并测量（无 /usr/bin/time 的环境用自带 measure.py）
python3 tools/measure.py -- python3 -m dicttool build \
    --src /tmp/words.jsonl --out /tmp/dict.mdawg --mem-budget 33554432
python3 tools/measure.py -- python3 -m dicttool check --dict /tmp/dict.mdawg --deep
python3 tools/measure.py -- python3 -m dicttool suggest \
    --dict /tmp/dict.mdawg --text cleantion --max-edits 2 --k 10 --stats
```

参考实测（本仓库开发机，Python 3.11）：100 万行 → 173,416 唯一词
（826,584 个重复权重被求和）；trie 需 743,065 节点，最小自动机
126,919 状态 / 287,957 弧（5.9×）；包 11.2MB；构建 12s / 峰值
140MiB；`check --deep` 4.3s；`prefix "data_" k=5` 仅 23 次展开即停；
`suggest e=2 k=5` 88 次展开。`verify --random-queries 12` 全部通过。

高扇出上界停止的证据见 `tests/test_fanout_bound.py`：5 万个 weight=1
的 `qXXXXXX` 词 + 一个 weight=10^9 的词，`prefix("",1)` 与
`suggest("zzz",0,1)` 的展开数 ≤10、权重查找 ≤2，不读低权重分支。

## 6. 输入规则

每行一个 JSON 对象：`word`（非空字符串，≤ `--max-word-len` 码点，
禁止孤立代理码点 U+D800–U+DFFF）与 `weight`（非负整数，≤ 2^64-1；
布尔/浮点/字符串一律拒绝）。相同 word 的权重相加（溢出报错）。
Unicode 一律按码点比较与计数，不做大小写折叠、不做正规化折叠
（U+00E9 与 U+0065 U+0301 是两个不同的词）。空行、非对象行、缺键
均报错并给出行号。

## 7. 已知限制（如实说明）

- 最小化后的图在内存中构建（词表本身外排，不入内存）；
- 打开包做全量校验，代价 O(包大小)；
- 权重上界相等且拼写交错的子树会引起额外展开（统计可见，非剪枝
  伪装）；
- suggest 阈值为 0–2（规格要求），查询文本 ≤ 4096 码点；
- 前沿超额定即失败，需调大 `--max-frontier` 重试（确定性行为）。
