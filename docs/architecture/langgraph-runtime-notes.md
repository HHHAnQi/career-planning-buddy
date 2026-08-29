# LangGraph Runtime 原理笔记（踩坑实证版）

> 用途：面试答辩「你懂框架内部吗」的核心弹药。每个机制点都配本项目
> 真实踩过的坑与定位过程——机制 + 实证 + 排查路径三段式。
> 环境：langgraph 1.2.10 / Python 3.13 / SQLAlchemy async。

---

## 1. Pregel / BSP 超步模型

**机制**：LangGraph 的执行内核是 Bulk Synchronous Parallel——同一超步
（superstep）内的节点**并发执行**，channel 写入在超步边界统一合并；
step N 的写只在 step N+1 对其他节点可见。`PregelRunner.atick` 用
`asyncio.wait(FIRST_COMPLETED)` 驱动，节点各自是独立 asyncio task。

**实证**：fan-out 改造后 `tests/test_parallel_context_fanout.py` 里两个
loader 节点的 instrumented 执行区间重叠 >30ms——同一超步并发直接可测。

**答辩要点**：这解释了为什么 join 节点（context_builder）天然等到两个
分支都写完才执行——不是我们写了 barrier，是超步边界就是 barrier。

## 2. Channel 语义与并发写冲突

**机制**：TypedDict state 的每个 key 是一个 channel。默认类型
`LastValue`——**同超步两个节点写同一个 key 直接抛
`InvalidUpdateError: Can receive only one value per step**，提示改用
`Annotated[key, reducer]`。要允许并发写必须显式给 channel 配 reducer
（如 `operator.add`）。

**实证**：我们用最小复现确认过——两个并行节点写同一个 `log` key 报错。
因此 fan-out 双 loader 的设计约束是**写不相交的 key**（memory_parcel /
history_parcel），这是拓扑正确性的前提，不是巧合。

## 3. path_map vs 路由函数返回列表

**机制**：`add_conditional_edges(src, router, path_map)` 的 path_map 值
必须是**单个节点名**（compile 时进 `validate()` 的节点集合校验，list 是
unhashable 直接 `TypeError`）。要 fan-out，让**路由函数本身返回节点名
列表**——返回值作为目的地集合展开，无 path_map。

**实证**：先写 `"ready": ["memory_loader", "evidence_loader"]` 进
path_map，`compile()` 立刻 `TypeError: unhashable type: 'list'`；改成
`_route_after_intent` 返回列表后编译通过、并发生效。

## 4. 未声明的 state key 被静默丢弃

**机制**：channels 由 state schema（TypedDict）构建。节点返回的 dict 里
**任何未在 schema 声明的 key 没有目标 channel，写入被静默吞掉**——不报
错、不留痕。下游节点读不到该 key，表现为 KeyError 或默认值。

**实证**：`memory_parcel` / `history_parcel` 忘了加进 PlanningState
TypedDict 时，loader 正常返回、merge 节点却拿空 parcel，下游
`planning_context` KeyError。排查两小时，最终在 merge 打印 state keys 才
定位——这个坑的教训：**fan-out 节点的新增 state key 必须先声明 schema**。

## 5. 节点异常 → 兄弟任务被取消

**机制**：同超步内任一 task 异常，`_panic_or_proceed` 会 `cancel()` 所有
inflight 兄弟 task 再抛原异常。所以**兄弟分支的 CancelledError 往往不是
自己的 bug，是另一个分支炸了**。

**实证**：`_immediate` 是 async staticmethod，一处少了 `await` 返回裸
coroutine，NodeRunner 访问 `.telemetry` 抛 AttributeError——该分支失败，
另一个 loader 分支收到 CancelledError。表象（兄弟被取消）与根因（另一个
分支的 AttributeError）隔着一层，靠 graph-level spy 捕获原始异常才串起来。

## 6. 我们的图与框架能力的边界（诚实清单）

| 用的 | 没用的 | 为什么不用 |
|---|---|---|
| 条件边、fan-out/join、有界回边 | interrupt / human-in-the-loop | 业务无中断审批场景 |
| TypedDict state + 不相交 key 写入 | Annotated reducer 并发写 | 双分支天然写不同 key |
| 自研 Postgres checkpoint（与业务 lease/attempt 联动） | 框架内置 SqliteSaver/PostgresSaver | 恢复语义要感知 Run 的 deadline 与取消标志 |
| 编译期 topology 断言测试 | LangSmith 集成 | 自研 trace 已覆盖 run/node/call 三级 |

## 7. 一句话总结（面试收尾用）

「我不是把 LangGraph 当黑盒 DSL 用——超步并发、channel 冲突、条件路由
的两种形态、schema 外 key 的静默丢弃、异常传导取消兄弟任务，这五个机制
我全部踩过坑并用最小复现确认过机制。选它是因为恢复语义我换了持久层、
其余内核直接复用；不用它的部分（reducer、interrupt、内置 Saver）我也能
说清为什么不需要。」
