# PRD: Dify 工作流节点 Span 命名细分

> 来源工作项: [AGE-42](mention://issue/be600c9c-b2fb-4cba-81ff-7d0a3ddd3b0d) "Dify工作流Span埋点优化"
> 提出方: 安全护栏团队
> 目标仓库: `~/dev_musi/dify`(代码仓 `https://code.alibaba-inc.com/agentloop/agentloop_dev.git`,基线分支 `main`,需求要求"拉出新分支"实现)

## 1. Background & Context

Dify 已通过 `ObservabilityLayer`(`api/core/app/workflow/layers/observability.py`)将工作流节点的执行过程作为 OpenTelemetry span 上报至 ARMS 链路追踪。当前实现存在如下两类"信息塌缩"问题,导致安全护栏团队和其他下游消费方无法在链路中快速识别节点的实际语义:

1. **Span name 单一化为 `node.title`**
   `observability.py:109` 将 span 名直接取为 `f"{node.title}"`。在大量画布上,用户既未改名,也常常使用泛化的中文描述(例如默认就叫"代码"、"任务"、"分支"),无法从名字判断这是 `code`、`if-else`、`http-request` 还是 `loop`。
2. **`gen_ai.span.kind` 语义粒度过粗**
   `DefaultNodeOTelParser.parse()`(`api/extensions/otel/parser/base.py:107-115`)只对 `LLM` / `KNOWLEDGE_RETRIEVAL` / `TOOL` 三类节点设置专门的 `gen_ai.span.kind`,其余 16+ 种节点(`IF_ELSE`、`LOOP`、`ITERATION`、`CODE`、`HTTP_REQUEST`、`TEMPLATE_TRANSFORM`、`ANSWER`、`LIST_OPERATOR`、`VARIABLE_ASSIGNER`、`LEGACY_VARIABLE_AGGREGATOR`、`PARAMETER_EXTRACTOR`、`QUESTION_CLASSIFIER`、`DOCUMENT_EXTRACTOR`、`AGENT`、`HUMAN_INPUT`、`DATASOURCE`、`START`、`END` 等)统一落到 `TASK` 字符串。这正是工单中"所有 Dify 相关 span 名称都统一叫 task"的来源——用户实际看到的是 `gen_ai.span.kind=TASK` 的同质化字符串桶。

阿里集团对外开源的 LoongSuite [`opentelemetry-util-genai`](https://github.com/alibaba/loongsuite-python-agent/tree/main/util/opentelemetry-util-genai) 提供了 GenAI 链路统一的语义约定与一组 invocation-typed 的 Span helper(`ExtendedTelemetryHandler.llm()` / `retrieval()` / `execute_tool()` / `invoke_agent()` / `embedding()` / `entry()` / `react_step()` / `memory()` …)。本期把该包(PyPI 名 `loongsuite-util-genai`,导入命名空间 `opentelemetry.util.genai`)作为运行期依赖引入 Dify,并把它升级为 **Span 发射器**:LLM / 检索 / 工具 / Agent 等已存在专属语义的节点,改为通过 `ExtendedTelemetryHandler` 的 invocation 上下文管理器来产生 Span,以最大化贴合 GenAI 语义约定;其余工作流控制/工具节点(if-else、loop、code、http-request 等),由于 util-genai 暂未提供等价 helper,继续走 `tracer.start_span` 但语义常量由 util-genai 统一供给。

本期**优先实现 ObservabilityLayer 这一条路径**;`aliyun_trace` provider(`api/providers/trace/trace-aliyun/.../aliyun_trace.py`)的服务端重建链路上的同等改造在本期暂不实现,挪到 Future Work 排期(详见 N1)。

兼容性策略以 attribute 字段而非运行期开关承载:`gen_ai.framework=dify` / `node.type=<kind>` / 现有 LLM·RETRIEVER·TOOL 三类 `gen_ai.span.kind` 取值保持不变(util-genai helper 写出的 `gen_ai.span.kind` 与现状一致,LLM helper 写 `LLM`,retrieval helper 写 `RETRIEVER`,execute_tool helper 写 `EXECUTE_TOOL`——后者与 Dify 历史 `TOOL` 字符串存在差异,需在 PR 中显式记入下游迁移指引,详见 F7)。新值集合是增量扩展;`gen_ai.span.kind=TASK` 等值匹配的下游规则需要由消费方一次性迁移到取值集合匹配,本期不提供运行期回退开关。

为了在生产形态上对 util-genai 升级方案做端到端验证,本期还需要一个 E2E 测试通道:在 `~/.kube/config-hk` 集群的 `dify-system` 命名空间内部署改造后的 Dify(API + Worker),把 OTel exporter 切到 `console`(标准输出),由 E2E 测试用例触发一个混合节点工作流并校验 stdout 中的 Span 集合(详见 F10 / AC-010 / Test Plan E2E 行)。

## 2. Goals & Non-Goals

**Goals(P0)**:

- G1. **节点级 span 可识别**:对 Dify 画布上的每一种内置节点类型(`graphon.enums.BuiltinNodeTypes` 中目前已知的 21 种,见附录 A),为其工作流执行 span 提供稳定的、可在 ARMS / Grafana / 自研查询面板上做精准过滤的字符串标识(形如 `dify:if-else`、`dify:code`、`dify:http-request`)。
- G2. **保留原始节点类型作为 attribute**:在 span 上保留独立的、可被检索的节点类型 attribute,用于后续按类型聚合查询。
- G3. **向后兼容(查询不破坏)**:不删除现有的 `gen_ai.span.kind` / `node.type` / `gen_ai.framework` 等已上报字段;已经按这些字段建立看板、告警、护栏规则的下游不需要立即改造(下游迁移指引见 F7)。
- G4. **以 LoongSuite util-genai 为运行期 Span 发射器**:LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点改为通过 `ExtendedTelemetryHandler` 的 invocation 上下文管理器(`handler.llm()` / `retrieval()` / `execute_tool()` / `invoke_agent()`)产生 Span,以贴合 GenAI 语义约定;Dify 现有的 `gen_ai.*` attribute 经 invocation 字段写入,不再用本仓库私有的 `set_attribute` 调用堆迭。
- G5. **本期通过 E2E 验证实跑链路**:在 ACK(`~/.kube/config-hk`)的 `dify-system` 命名空间部署改造后的 Dify,启用 OTel `console` exporter,跑一个覆盖关键节点类型的工作流,并断言 stdout 中 Span 命名/属性符合本规格。

**Non-Goals**:

- N1. **不在本期改造 `aliyun_trace` provider**(`api/providers/trace/trace-aliyun/.../aliyun_trace.py`)。`build_workflow_node_span` / `build_workflow_task_span` 服务端重建路径暂不引入新命名方案;同等改造移到 Future Work 排期。本期仅保证 OTel 实时上报路径(`ObservabilityLayer` + `extensions/otel/parser/*`)的命名细分。
- N2. **不改造其他第三方 trace provider**(arize-phoenix / langfuse / langsmith / tencent / weave 等)。这些 provider 自己的 span 模型由其上游 SaaS 决定,本期不强行统一。
- N3. **不调整 span 父子结构 / span kind(`SpanKind.INTERNAL` 等 OTel 一级枚举) / 资源属性**。本期只动 span name 和节点级 attribute(util-genai helper 默认产 `CLIENT` / `RETRIEVER` 等 SpanKind,本期予以接受,作为对原 `INTERNAL` 的语义升级,见第 4 节非功能"兼容性"段)。
- N4. **不修改 Dify 前端展示**(画布、运行历史 UI 不受影响)。
- N5. **不修改自定义节点(plugin / extension 提供的非内置节点)的命名规则**。自定义节点保留兜底逻辑(F5),本期不做新约定;如需扩展,用 attribute 透出 `node_type` 原值即可。
- N6. **不引入运行期灰度/回滚开关**。新命名直接生效,兼容性由 attribute 增量扩展承载;消费方对 `gen_ai.span.kind=TASK` 等值匹配的下游规则迁移由 F7(文档)指引一次性完成,不再提供按租户/集群灰度发布的运行期通道。
- N7. **不替换已配置的全局 TracerProvider / Resource / Sampler**。`api/extensions/ext_otel.py` 在启动期注册的 `TracerProvider`、`Resource` attributes、`ParentBasedTraceIdRatio` 采样策略保持不变,util-genai 通过 `get_extended_telemetry_handler()` 默认读取全局 Provider,不再额外构造。

## 3. Functional Requirements

| ID | Feature | Description | Priority |
|----|---------|-------------|----------|
| F1 | 节点 span name 改写 | `ObservabilityLayer.on_node_run_start` 创建 span 时,name 由 `f"{node.title}"` 改为 `f"dify:{<canonical-node-kind>}"`(canonical 形式见 F4)。原 `node.title` 通过 attribute(F3)单独保留。LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点的 span 由 util-genai helper(F8)发射,其原生 span name(如 `chat <model>` / `retrieval <data_source>` / `execute_tool <tool>` / `invoke_agent <agent>`)在 invocation finish 阶段改写为 `dify:<canonical-kind>`,以保持本期统一命名口径;原生命名通过 attribute(`gen_ai.original.span.name`)保留以便下游回查。 | P0 |
| F2 | `gen_ai.span.kind` 细分 | LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点的 `gen_ai.span.kind` 由 util-genai helper 自动写入(`LLM` / `RETRIEVER` / `EXECUTE_TOOL` / `INVOKE_AGENT`,具体取值以 util-genai 实际写出的常量为准)。其余节点(workflow 控制/工具类)由 `DefaultNodeOTelParser` 显式写为 `WORKFLOW_<KIND>`(具体值表与附录 A 一致)。本表替代历史"统一 TASK"行为。 | P0 |
| F3 | 新增节点类型 attribute | 在所有节点 span 上写入两个新 attribute:`dify.node.kind`(canonical kebab-case,例如 `if-else`、`http-request`)与 `dify.node.title`(原用户自定义标题)。`node.type` 字段保留兼容(写入相同的 canonical 值)。 | P0 |
| F4 | 节点类型→canonical kind 映射表 | 在 `extensions/otel/semconv/` 下新增**唯一**映射函数 `node_type_to_kind(node_type: BuiltinNodeTypes) -> str`,覆盖附录 A 全部 21 种 BuiltinNodeTypes;返回值为 kebab-case 字符串。F1/F2/F3 全部读自该函数。 | P0 |
| F5 | 自定义/未知节点兜底 | 当 `node.node_type` 不在映射表中(自定义插件、未来新增节点),span name 落为 `dify:custom`,`dify.node.kind=custom`,并把原始 `node_type` 字符串透出到 `dify.node.kind.raw`,`gen_ai.span.kind` 沿用 `TASK`。不报错、不丢链路。 | P0 |
| F6 | aliyun_trace 路径暂缓改造 | 本期不修改 `api/providers/trace/trace-aliyun/.../aliyun_trace.py` 的 `build_workflow_task_span` / `build_workflow_node_span`。为避免 OTel 实时路径与服务端重建路径短暂的命名不一致,需要在文档(F7)与实施 PR 描述里**显式提示下游**:`aliyun_trace` provider 重建链路命名将在后续工作项中跟进;在过渡期内同时订阅两条路径的看板需自行处理命名差异。 | P0 |
| F7 | 文档与下游迁移指引 | 更新 `docs/` 下与可观测性相关的页面(若不存在则新增 changelog/迁移说明)。文档内容包含:(a) canonical kind 取值集合;(b) 新 attribute 字典;(c) 新版 `gen_ai.span.kind` 取值集合(`WORKFLOW_*` + util-genai 写出的 `LLM` / `RETRIEVER` / `EXECUTE_TOOL` / `INVOKE_AGENT`);(d) 对下游"`gen_ai.span.kind=TASK` 等值匹配 → 取值集合匹配"的一次性迁移指引;(e) `aliyun_trace` provider 暂未跟进的过渡期提示;(f) 历史 `gen_ai.span.kind=TOOL` 与 util-genai `EXECUTE_TOOL` 的命名差异提示。**本期不提供运行期回退开关**,文档须明确这一点,要求消费方在文档发布后一次性切换查询/告警规则。 | P1 |
| F8 | util-genai Span 发射改造 | 在 `api/pyproject.toml` 中新增 `loongsuite-util-genai` 运行期依赖(导入命名空间 `opentelemetry.util.genai`)并刷新 `uv.lock`。改造范围:① 在 `extensions/otel/semconv/` 下从该包 `import` 本期所需的 attribute / 枚举常量(LLM、Retrieval、Tool、Agent 相关 `gen_ai.*` 键名;若上游已提供 `WORKFLOW_*` / `TASK` 等取值常量,直接复用;未提供的暂在 `semconv/` 内定义本地 fallback,加注"待上游补齐后切换")。② `extensions/otel/parser/llm.py` / `retrieval.py` / `tool.py` 由"在调用方传入的 Span 上 `set_attribute`"重构为"构造对应的 invocation 对象(`LLMInvocation` / `RetrievalInvocation` / `ExecuteToolInvocation` / `InvokeAgentInvocation`)并通过 `ExtendedTelemetryHandler.start_*` / `stop_*` / `fail_*` 管理生命周期"。③ `core/app/workflow/layers/observability.py` 的 `on_node_run_start` / `on_node_run_end` 改造为:对 LLM / Retrieval / Tool / Agent 节点用 util-genai handler 启停 Span;对其余节点继续使用全局 `tracer.start_span`(由 ext_otel 注册的全局 TracerProvider 提供),并通过 `DefaultNodeOTelParser` 写出 `WORKFLOW_*` 等 attribute。④ `get_extended_telemetry_handler()` 必须以**懒加载单例**形式获取(进程内缓存),不传 `tracer_provider`/`logger_provider`,沿用全局 Provider。⑤ util-genai helper 抛出的异常一律走 `ObservabilityLayer` 现有 `try/except + logger.warning` 容错路径,不得传播至工作流执行栈。详细使用与改造计划见附录 C。 | P0 |
| F9 | invocation 字段映射 | `LLMNodeOTelParser.parse()` 现在写入 Span 的字段(`gen_ai.request.model` / `gen_ai.provider.name` / `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `gen_ai.usage.total_tokens` / `gen_ai.input.messages` / `gen_ai.output.messages` / `gen_ai.response.finish_reason` 等)必须改为按 `LLMInvocation` 的 dataclass 字段(`request_model` / `provider` / `input_tokens` / `output_tokens` / `input_messages` / `output_messages` / `output_messages[*].finish_reason`)赋值,由 util-genai 在 `stop_llm` 时统一写出。Retrieval / Tool / Agent 同此原则,字段对照见附录 C。EE/CE 内容门控(`should_include_content()`)在 invocation 字段赋值前判定,门控为 False 时不把 prompt / output / messages / documents 等正文写入 invocation。 | P0 |
| F10 | E2E 部署与 stdout 验证 | 在仓库 `dev/k8s-e2e/dify-system/`(新目录)下提供一组 K8s manifest(API Deployment + Worker Deployment + Service + ConfigMap;DB / Redis / Vector store 沿用 `dify-system` 命名空间内已有的依赖,无则用 `Bitnami` 系列轻量镜像作内嵌依赖)与一份 `Makefile` 目标(`make e2e-dify-otel-console`)。该目标必须:① 用 `KUBECONFIG=~/.kube/config-hk` 把改造后的 Dify 镜像部署到 `dify-system` 命名空间;② 通过环境变量启用 OTel(`ENABLE_OTEL=True`,`OTEL_EXPORTER_TYPE=console`,`OTEL_SAMPLING_RATE=1.0`);③ 运行一个预置的混合节点工作流(start → if-else → code → http-request → llm → end);④ 抓取 `kubectl logs` 中的 Span 行,断言至少出现 `dify:start` / `dify:if-else` / `dify:code` / `dify:http-request` / `dify:llm` / `dify:end`,且 `gen_ai.span.kind` 集合落在附录 A 的合法取值集合内;⑤ 用例可重复执行,部署清理由 Makefile 兜底(`kubectl delete -k ...`)。本 E2E 不接入 ARMS / OTLP collector,stdout 即唯一断言来源。 | P0 |

### Boundaries and Exception Handling

- **空 / 异常输入**:`node.title` 为空、`node.execution_id` 为空、`node.node_type` 拿不到时,F1/F3 落到 F5 自定义兜底,不抛异常。
- **EE 内容门控**:F1–F6 / F8 均不涉及 input/output 内容字段;F9 在 invocation 字段赋值前判定 `should_include_content()`,门控为 False 时不把正文写入 invocation。功能行为在 CE / EE 一致。
- **`ENABLE_OTEL=False`**:`ObservabilityLayer._is_disabled=True` 时本期改动整体不生效,与现有逻辑保持一致。util-genai handler 的获取放到 `_is_disabled=False` 分支内,避免在禁用 OTel 的部署上构造 handler。
- **多语言 title**:用户在 Dify 画布上输入中文/Emoji 标题时,`dify.node.title` 直接落原值;span name 不再依赖该字段,因此不会出现旧版本上 ARMS 端按字符串模糊检索失败的问题。
- **`gen_ai.span.kind` 取值集合扩展**:扩充后的取值需要列在 spec / docs 中并提示下游(护栏规则、看板)按集合而非等值匹配;现有等值匹配 `gen_ai.span.kind=TASK` 的老查询在非 LLM/Retriever/Tool 节点上不再命中,需按 F7 文档一次性迁移到取值集合匹配。
- **util-genai helper 异常**:`ExtendedTelemetryHandler` 内部异常(invocation 构造失败、Span 启停失败、context detach 失败等)由 `ObservabilityLayer` 的 `try/except` 兜底,降级回 `tracer.start_span` 直发 Span,保证工作流执行不被阻断;降级路径必须仍写出 `dify.node.kind` / `dify.node.title` / `node.type`,以保证下游查询不丢字段。

## 4. Non-Functional Requirements

- **性能**:F1–F4 都是常量时间字符串映射;F8 / F9 的 invocation 构造不引入 IO/锁,util-genai helper 内部走与 `tracer.start_span` 同源的 OTel SDK,延迟与现状同阶。`ObservabilityLayer` 现有 try/except 保护沿用,不允许因新增逻辑导致工作流执行失败。
- **可观测性**:`logger.warning` 仅在映射函数未命中(F5 兜底分支)或 util-genai helper 抛错(F8 降级分支)时打印一次/启动周期(避免每次执行都刷日志),原有 warning 不动。
- **测试**:
  - 新增/扩展 `api/tests/unit_tests/core/workflow/graph_engine/layers/test_observability.py`,覆盖每一种 BuiltinNodeTypes 的 span name 与 attribute(参数化用例);用 `InMemorySpanExporter` 校验 util-genai 写出的 Span 命名/属性。
  - 现有 `test_observability.py` 中断言 `spans[0].name == mock_llm_node.title` 的用例(同文件 line 63)需要随 F1 一起更新为 `dify:<kind>`。
  - 新增 invocation 字段映射用例(`LLMInvocation` / `RetrievalInvocation` / `ExecuteToolInvocation` 字段值与现状 `gen_ai.*` attribute 取值的等价性)。
  - 新增 util-genai helper 抛错的降级分支用例(模拟 `start_llm` 抛 `RuntimeError`,断言降级到 `tracer.start_span` 后的 Span attribute 仍完整)。
  - 新增 E2E 测试目标 `make e2e-dify-otel-console`(F10),CI 中默认 skip(标记 `@pytest.mark.e2e`),由实施 Agent 在合并前手工跑一次,日志归档到 PR 描述。
- **兼容**:任何已订阅 `gen_ai.framework=dify`、`node.type` attribute 的下游不受影响;只新增字段、不删减字段;`gen_ai.span.kind=TASK` 等值匹配的非 LLM/Retriever/Tool 节点查询规则在文档发布后由消费方一次性迁移。SpanKind 由 `INTERNAL` 升级为 `CLIENT` / `RETRIEVER` / `INTERNAL`(分别对应 LLM/Retrieval/其它),已在文档明示并在 F7 迁移指引中列入兼容性提示。
- **配置**:本期不新增运行期配置项,`api/configs/observability/otel/otel_config.py` 不在改动文件列表中。E2E 测试通道(F10)使用现有 `OTEL_EXPORTER_TYPE=console` 通路,不引入新的环境变量。
- **国际化与隐私**:span name 为 ASCII kebab-case 不含用户敏感数据;`dify.node.title` 受现有 `should_include_content()` 不控制(它是元数据非内容),符合现行隐私边界。

## 5. Dependencies & Assumptions

- **依赖**:
  - `graphon~=0.4.0`(已在 `api/pyproject.toml`),提供 `BuiltinNodeTypes`。本期不升级 graphon。
  - 新增 `loongsuite-util-genai` 运行期依赖(`api/pyproject.toml` + `uv.lock`),作为 Span 发射器供 LLM/Retrieval/Tool/Agent 节点使用,语义常量供其余节点引用。
  - 现有 `extensions.otel.semconv.gen_ai` / `dify` 模块。
  - `~/.kube/config-hk` 集群可用、`dify-system` 命名空间存在(已在 ACK 上预先创建,本期 E2E 复用)、可用的容器镜像仓库(用于 `make e2e-dify-otel-console` push 改造后的镜像,默认走开发镜像仓,如未配置则 fallback 为本地 `kind` / `minikube` 加载,实施 PR 中具体说明所选通道)。
- **假设**:
  - `BuiltinNodeTypes` 在 graphon 0.4.x 内的取值集合在本期内不会变化(若变化,F4 映射表需要补齐,F5 兜底保证不破坏)。
  - 安全护栏团队的查询/告警迁移以 `dify.node.kind` 或新版 `gen_ai.span.kind` 取值集合为主键,一次性切换。本期不提供运行期回退开关,迁移期需求方自行通过 `gen_ai.framework=dify` / `node.type=<kind>` 等不变 attribute 维持过渡查询。
  - 本期目标分支由实施 Agent 从 `main` 拉出(命名建议 `feat/dify-span-naming-AGE-42`,与工单号挂钩),需求方接受后续合并到 `main`。
  - `loongsuite-util-genai` 的 ExtendedTelemetryHandler API(`llm()` / `retrieval()` / `execute_tool()` / `invoke_agent()` 等上下文管理器与 invocation dataclass 字段)在实施 PR 期间稳定;若上游变更签名,实施 Agent 在 PR 中同步本规格附录 C 与 invocation 字段映射,必要时回到 `tracer.start_span` 直发 + 语义常量的方案,F1~F7 / F10 不受影响。

---

## 附录 A: BuiltinNodeTypes → canonical kind 候选映射表

| BuiltinNodeTypes 枚举名 | canonical kind (`dify.node.kind`) | span name | `gen_ai.span.kind`(util-genai 写入或本仓库写入) |
|----|----|----|----|
| `START` | `start` | `dify:start` | `WORKFLOW_START` (本仓库) |
| `END` | `end` | `dify:end` | `WORKFLOW_END` (本仓库) |
| `ANSWER` | `answer` | `dify:answer` | `WORKFLOW_ANSWER` (本仓库) |
| `LLM` | `llm` | `dify:llm` | `LLM` (util-genai) |
| `KNOWLEDGE_RETRIEVAL` | `knowledge-retrieval` | `dify:knowledge-retrieval` | `RETRIEVER` (util-genai) |
| `TOOL` | `tool` | `dify:tool` | `EXECUTE_TOOL` (util-genai;与历史 `TOOL` 不同,见 F7 迁移指引) |
| `AGENT` | `agent` | `dify:agent` | `INVOKE_AGENT` (util-genai;原为 `TASK`,迁移至专属 kind) |
| `IF_ELSE` | `if-else` | `dify:if-else` | `WORKFLOW_IF_ELSE` (本仓库) |
| `ITERATION` | `iteration` | `dify:iteration` | `WORKFLOW_ITERATION` (本仓库) |
| `LOOP` | `loop` | `dify:loop` | `WORKFLOW_LOOP` (本仓库) |
| `CODE` | `code` | `dify:code` | `WORKFLOW_CODE` (本仓库) |
| `TEMPLATE_TRANSFORM` | `template-transform` | `dify:template-transform` | `WORKFLOW_TEMPLATE` (本仓库) |
| `HTTP_REQUEST` | `http-request` | `dify:http-request` | `WORKFLOW_HTTP` (本仓库) |
| `LIST_OPERATOR` | `list-operator` | `dify:list-operator` | `WORKFLOW_LIST` (本仓库) |
| `VARIABLE_ASSIGNER` | `variable-assigner` | `dify:variable-assigner` | `WORKFLOW_VAR` (本仓库) |
| `LEGACY_VARIABLE_AGGREGATOR` | `legacy-variable-aggregator` | `dify:legacy-variable-aggregator` | `WORKFLOW_VAR` (本仓库) |
| `PARAMETER_EXTRACTOR` | `parameter-extractor` | `dify:parameter-extractor` | `WORKFLOW_PARAM_EXTRACT` (本仓库) |
| `QUESTION_CLASSIFIER` | `question-classifier` | `dify:question-classifier` | `WORKFLOW_CLASSIFY` (本仓库) |
| `DOCUMENT_EXTRACTOR` | `document-extractor` | `dify:document-extractor` | `WORKFLOW_DOC_EXTRACT` (本仓库) |
| `HUMAN_INPUT` | `human-input` | `dify:human-input` | `WORKFLOW_HUMAN_INPUT` (本仓库) |
| `DATASOURCE` | `datasource` | `dify:datasource` | `WORKFLOW_DATASOURCE` (本仓库) |
| (未知 / 自定义节点) | `custom` | `dify:custom` | `TASK` (本仓库) |

> **注 1**:`gen_ai.span.kind` 标注为 "util-genai" 的行,取值以 util-genai `ExtendedTelemetryHandler` 在 `stop_*` 时写出的实际常量为准。若 util-genai 写出的字符串与本表不一致,以 util-genai 为准并同步修订本表,这种情况由实施 PR 评审环节最终确认。
> **注 2**:`WORKFLOW_*` 系列由本仓库 `extensions/otel/semconv/` 维护;若 util-genai 后续提供同等常量,实施时切换为 `import` 上游常量并删除本地 fallback。

---

## 附录 B: util-genai 关键 API 摘要(供 F8 / F9 / 附录 C 参考)

- 包名(PyPI):`loongsuite-util-genai`(LoongSuite 发行版,推荐;社区上游版 `opentelemetry-util-genai` 不含 retrieval / execute_tool / invoke_agent helper,本期不采用)。
- 导入命名空间:`opentelemetry.util.genai`。
- Handler 入口:`from opentelemetry.util.genai.extended_handler import get_extended_telemetry_handler`,返回单例 `ExtendedTelemetryHandler`。不传 `tracer_provider` / `logger_provider` 时使用全局 Provider。
- 上下文管理器(本期使用):
  - `handler.llm(invocation: LLMInvocation)` — 产 `gen_ai.span.kind=LLM`,默认 SpanKind=CLIENT。
  - `handler.retrieval()` — 产 `gen_ai.span.kind=RETRIEVER`,默认 SpanKind=RETRIEVER。
  - `handler.execute_tool()` — 产 `gen_ai.span.kind=EXECUTE_TOOL`。
  - `handler.invoke_agent()` — 产 `gen_ai.span.kind=INVOKE_AGENT`。
- 生命周期等价低阶 API(用于 `ObservabilityLayer.on_node_run_start` / `on_node_run_end` 拆分):`handler.start_*(invocation)` / `handler.stop_*(invocation)` / `handler.fail_*(invocation, Error(type=..., message=...))`。
- invocation dataclass 关键字段(详见附录 C):
  - `LLMInvocation`:`request_model` / `provider` / `operation_name` / `input_messages` / `output_messages` / `system_instruction` / `input_tokens` / `output_tokens` / `attributes`。
  - `RetrievalInvocation`:`query` / `documents`(`RetrievalDocument`) / `data_source_id` / `provider` / `top_k` / `attributes`。
  - `ExecuteToolInvocation`:`tool_name` / `tool_type` / `tool_call_arguments` / `tool_call_result` / `tool_description` / `provider` / `attributes`。
  - `InvokeAgentInvocation`:`agent_name` / `agent_id` / `request_model` / `input_messages` / `output_messages` / `provider` / `attributes`。
- 环境变量(本期文档需提示运维):
  - `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` — 启用实验性语义约定。
  - `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT|SPAN_ONLY|EVENT_ONLY|SPAN_AND_EVENT` — 控制是否把 prompt / output 等正文写入 Span(默认 `NO_CONTENT`)。Dify 侧通过 `should_include_content()` 进一步门控。

---

## 附录 C: util-genai 节点级使用与改造计划(对应 F8 / F9)

下表给出 21 种 BuiltinNodeTypes(+ 自定义兜底)在本期的 Span 发射通道与 invocation 字段映射,供 `extensions/otel/parser/*` 与 `core/app/workflow/layers/observability.py` 实施时参照。"通道"列含义:

- **util-genai/llm**:经 `handler.llm(invocation)` 启停 Span,LLM 专属 attribute 经 `LLMInvocation` 字段写入。
- **util-genai/retrieval**:经 `handler.retrieval()` 启停 Span。
- **util-genai/execute_tool**:经 `handler.execute_tool()` 启停 Span。
- **util-genai/invoke_agent**:经 `handler.invoke_agent()` 启停 Span。
- **tracer.start_span**:由全局 TracerProvider 直接发射 Span(util-genai 暂未提供等价 helper),`gen_ai.span.kind` 由 `DefaultNodeOTelParser` 显式写入 `WORKFLOW_*`。

| BuiltinNodeTypes | 通道 | 说明 |
|----|----|----|
| `LLM` | util-genai/llm | `LLMNodeOTelParser` 改造为构造 `LLMInvocation`(`request_model = process_data["model_name"]`,`provider = process_data["model_provider"]`,`input_tokens / output_tokens` 来自 `usage_data`,`input_messages` 由现有 `_format_input_messages` 解析 prompts → `InputMessage(role, parts=[Text(content)])`,`output_messages` 由 `_format_output_messages` 解析 outputs → `OutputMessage(role="assistant", parts=[Text(content)], finish_reason)`)。EE 内容门控为 False 时不写入 messages 字段。 |
| `KNOWLEDGE_RETRIEVAL` | util-genai/retrieval | `RetrievalNodeOTelParser` 改造为构造 `RetrievalInvocation`(`query = inputs["query"]`,`documents` 由现有 `_format_retrieval_documents` 输出 → `RetrievalDocument(id, score, content, metadata)`,`data_source_id` 来自 `node.data.dataset_ids` 等元数据)。 |
| `TOOL` | util-genai/execute_tool | `ToolNodeOTelParser` 改造为构造 `ExecuteToolInvocation`(`tool_name = node.title or node.tool_name`,`tool_type = tool_data.provider_type.value`,`tool_call_arguments = node_run_result.inputs`,`tool_call_result = node_run_result.outputs`,`tool_description` 由 metadata 中 `TOOL_INFO` 序列化)。**注**:util-genai 写出的 `gen_ai.span.kind=EXECUTE_TOOL`,与 Dify 历史 `TOOL` 不一致,需在 F7 文档/PR 描述中明示。 |
| `AGENT` | util-genai/invoke_agent | 现状无独立 parser,本期新增 `AgentNodeOTelParser`,构造 `InvokeAgentInvocation`(`agent_name`、`agent_id`、`request_model`、`input_messages` / `output_messages`)。若 Agent 节点信息暂无法填齐,允许只填 `agent_name=node.title`,其余字段缺省,实施 PR 中以单独 commit 标注 TODO。 |
| `IF_ELSE` / `ITERATION` / `LOOP` / `CODE` / `TEMPLATE_TRANSFORM` / `HTTP_REQUEST` / `LIST_OPERATOR` / `VARIABLE_ASSIGNER` / `LEGACY_VARIABLE_AGGREGATOR` / `PARAMETER_EXTRACTOR` / `QUESTION_CLASSIFIER` / `DOCUMENT_EXTRACTOR` / `HUMAN_INPUT` / `DATASOURCE` / `START` / `END` / `ANSWER` | tracer.start_span | 走全局 TracerProvider 的 `start_span` 路径。span name = `dify:<kind>`,`gen_ai.span.kind = WORKFLOW_<KIND>`,`dify.node.kind` / `dify.node.title` / `node.type` / `gen_ai.framework=dify` 由 `DefaultNodeOTelParser` 显式写入。本通道与现状逻辑保持等价,只改 span name 与 `gen_ai.span.kind` 取值。 |
| 自定义 / 未知节点 | tracer.start_span | F5 兜底:span name=`dify:custom`,`gen_ai.span.kind=TASK`,`dify.node.kind.raw=<原始 node_type>`。 |

实施步骤(供实施 Agent 参照,不进入 spec 验收范围,仅作过程性指引):

1. 在 `extensions/otel/semconv/` 下新增 `node_kind.py`,提供 `node_type_to_kind`、`kind_to_workflow_span_kind` 两个纯函数,以及 `DIFY_NODE_TITLE` / `DIFY_NODE_KIND` / `DIFY_NODE_KIND_RAW` 三个 attribute 键名常量;若上游 util-genai 已暴露同名常量,直接 `import` 复用。
2. 在 `extensions/otel/runtime.py`(或新文件 `genai_handler.py`)中提供 `get_handler() -> ExtendedTelemetryHandler` 的懒加载单例,捕获构造异常并降级为 `None`(供调用方判空降级)。
3. 改造 `extensions/otel/parser/llm.py` / `retrieval.py` / `tool.py`:把"接收 Span + `set_attribute`"的 parse 协议拆为"构造 invocation + 由 `ObservabilityLayer` 启停 Span";`ObservabilityLayer.on_node_run_start` 中根据节点类型选择 `handler.start_llm(...)` 等;`on_node_run_end` 中根据成功/失败选择 `stop_*` / `fail_*`。
4. `DefaultNodeOTelParser.parse` 简化为只覆盖工作流控制/工具节点(F2 表格中的 `WORKFLOW_*` 行)。
5. 为 LLM/Retrieval/Tool/Agent 节点构造 invocation 时,若 `should_include_content()` 为 False,在 invocation 构造前清空 `input_messages` / `output_messages` / `query` / `documents` / `tool_call_arguments` / `tool_call_result` 等正文字段。
6. `ObservabilityLayer.on_node_run_end` 在 invocation finish 完成后,对 LLM/Retrieval/Tool/Agent 节点调用 `span.update_name(f"dify:{kind}")` 把 util-genai 写出的原生 span name 改写为本期统一命名口径,并写入 `gen_ai.original.span.name` attribute 保留原值(F1)。
7. 添加单元测试(参数化 22 组输入)与降级路径测试。
8. 添加 E2E manifest 与 Makefile 目标(F10)。
9. 更新文档(F7)。
