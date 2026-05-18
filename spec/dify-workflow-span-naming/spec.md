# SPEC: Dify 工作流节点 Span 命名细分

## 1. Meta

| 字段 | 内容 |
| --- | --- |
| Spec ID | FEATURE-001 |
| 状态 | Reviewing |
| Owner | 安全护栏团队 |
| 作者 | AgentLoop Spec Agent |
| 创建时间 | 2026-05-18 |
| 更新时间 | 2026-05-18 |
| 版本 | 1.3.0 |
| 类型 | Feature |

来源工作项: [AGE-42](mention://issue/be600c9c-b2fb-4cba-81ff-7d0a3ddd3b0d) "Dify工作流Span埋点优化"
基线代码仓: `https://code.alibaba-inc.com/agentloop/agentloop_dev.git`,本地工作目录 `~/dev_musi/dify`,基线分支 `main`(HEAD `e2c52c9b0f`,工作树干净)。建议实现分支命名 `feat/dify-span-naming-AGE-42`。

## 2. Context

Dify 已通过 `ObservabilityLayer` 把工作流节点的执行过程作为 OpenTelemetry span 上报至 ARMS。当前实现把 span 名直接取为用户在画布上自定义的 `node.title`,并且对绝大多数节点把 `gen_ai.span.kind` 笼统设为 `TASK`——只有 LLM/KNOWLEDGE_RETRIEVAL/TOOL 三种节点享有专属 kind。这导致下游消费方(尤其是发起本需求的安全护栏团队)在 ARMS / Grafana / 自研查询面板上无法稳定地区分条件分支、循环、HTTP 请求、代码执行、模板转换等十多种节点,所有非 LLM/Retriever/Tool 的节点全部塌缩成同一桶,告警规则与画像分析无法落地。

阿里集团对外的 LoongSuite [`opentelemetry-util-genai`](https://github.com/alibaba/loongsuite-python-agent/tree/main/util/opentelemetry-util-genai)(PyPI 名 `loongsuite-util-genai`,导入命名空间 `opentelemetry.util.genai`)给 GenAI 链路定义了统一的语义约定与一组 invocation-typed 的 Span helper(`ExtendedTelemetryHandler.llm()` / `retrieval()` / `execute_tool()` / `invoke_agent()` / `embedding()` / `entry()` / `react_step()` / `memory()`)。本期把该包**作为运行期依赖**引入 Dify,并把它升级为 **Span 发射器**:LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点改用对应的 `ExtendedTelemetryHandler` 上下文管理器产生 Span,以最大化贴合 GenAI 语义约定;其余工作流控制/工具节点(if-else、loop、code、http-request 等),由于 util-genai 暂未提供等价 helper,继续走全局 `tracer.start_span`,但语义常量(attribute 键名、若上游已提供则 `gen_ai.span.kind` 取值)统一从该包 `import`。

本期**优先实现 ObservabilityLayer 这一条路径**;`aliyun_trace` provider(`api/providers/trace/trace-aliyun/.../aliyun_trace.py`)的服务端重建链路同等改造在本期不实现,挪到 Future Work 排期(详见第 4 章)。

兼容性策略以 attribute 字段而非运行期开关承载:`gen_ai.framework=dify` / `node.type=<kind>` / 现有 LLM·RETRIEVER·TOOL 三类 `gen_ai.span.kind` 取值保持不变;新值集合是增量扩展;`gen_ai.span.kind=TASK` 等值匹配的下游规则需要由消费方一次性迁移到取值集合匹配,本期不再提供运行期回退开关。util-genai 写出的 `EXECUTE_TOOL`(原历史 `TOOL`)/ `INVOKE_AGENT`(原历史 `TASK`)与 Dify 历史值的命名差异,在 FR-008 文档与实施 PR 描述中显式列入下游迁移指引。

为在生产形态上端到端验证 util-genai 升级方案,本期还需要一个 E2E 测试通道:在 `~/.kube/config-hk` 集群的 `dify-system` 命名空间内部署改造后的 Dify(API + Worker),把 OTel exporter 切到 `console`(标准输出),由 E2E 用例触发一个混合节点工作流并校验 stdout 中的 Span 集合(详见 FR-010 / AC-009 / Test Plan E2E 行)。

## 3. Goal

- GL-001: 让任意一个 Dify 工作流节点的 OTel span 都能以稳定字符串识别其内置节点类型,使下游可在 ARMS 等链路系统按类型精准过滤、聚合、告警。
- GL-002: 在不破坏现有按 `gen_ai.framework=dify`、`node.type`、`gen_ai.span.kind` 已建立的看板与查询的前提下,新增更细粒度的节点类型 attribute。
- GL-003: 把 LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点的 Span 发射改造为通过 `loongsuite-util-genai` `ExtendedTelemetryHandler` 的 invocation 上下文管理器实现,以贴合 GenAI 语义约定;其余工作流控制/工具节点继续走全局 `tracer.start_span`,但语义常量统一从该包 `import`。
- GL-004: 通过 ACK(`~/.kube/config-hk`)`dify-system` 命名空间的实跑部署 + OTel `console` exporter 完成端到端验证,把"改造后链路是否正确写出新 span"这一问题在合并前关闭。

## 4. Out of Scope

- 不修改 arize-phoenix / langfuse / langsmith / tencent / weave 等其他第三方 trace provider 的 span 模型(它们由各自上游 SaaS 决定)。
- **不在本期改造 `aliyun_trace` provider**(`api/providers/trace/trace-aliyun/.../aliyun_trace.py`)。`build_workflow_node_span` / `build_workflow_task_span` 服务端重建路径暂不引入新命名方案;同等改造移到 Future Work(FW-1)排期。本期仅保证 OTel 实时上报路径(`ObservabilityLayer` + `extensions/otel/parser/*`)的命名细分。
- 不修改 OTel `SpanKind` 一级枚举的非升级行为:LLM 由 `INTERNAL` 升级为 util-genai helper 默认的 `CLIENT`,Retrieval 升级为 `RETRIEVER`,其余节点继续 `INTERNAL`。这是语义对齐带来的必要变化,不算违反本条;真正禁止的是引入 PRODUCER / CONSUMER / SERVER 等本期无关枚举。
- 不修改 span 父子结构、Resource 属性。
- 不修改 Dify 前端(画布、运行历史 UI 不受影响)。
- 不修改第三方插件 / 扩展提供的非内置节点的命名规则,统一走自定义节点兜底。
- 不修改 input/output 内容字段及其 EE/CE 内容门控(`ENTERPRISE_INCLUDE_CONTENT`)。invocation 字段写入前判定 `should_include_content()`,门控为 False 时不写入正文字段。
- **不引入运行期灰度/回滚开关**。新命名直接生效,兼容性由 attribute 增量扩展承载;消费方对 `gen_ai.span.kind=TASK` 等值匹配的迁移由文档(FR-008)指引一次性完成。
- **不替换全局 TracerProvider / Resource / Sampler**。`api/extensions/ext_otel.py` 在启动期注册的 Provider / Resource / `ParentBasedTraceIdRatio` 采样策略保持不变,util-genai 通过 `get_extended_telemetry_handler()` 默认读取全局 Provider。

### Future Work(本期不实现,记录用于排期)

- FW-1: `aliyun_trace` provider 服务端重建链路命名细分(`build_workflow_node_span` / `build_workflow_task_span`)。本期 `OTel 实时路径` 与 `aliyun_trace 路径` 短期内会出现命名不一致,需要 FR-008 文档显式提示;FW-1 跟进时复用本期 `node_type_to_kind` 函数与 attribute 集合。
- FW-2: 把 attribute 键名 `dify.node.kind` 升级为 `gen_ai.workflow.node.kind`(若 util-genai 给出对等键名),并保留 `dify.node.kind` 兼容写入 ≥1 个版本。
- FW-3: 评估是否把 `arize-phoenix` / `langfuse` / `langsmith` / `tencent` / `weave` provider 也纳入统一命名方案。
- FW-4: 等 util-genai 提供 `task()` / `workflow()` 等通用 helper 后,把 `WORKFLOW_*` 节点的发射也切换到 helper(本期工作流控制/工具节点继续走 `tracer.start_span`)。

## 5. Functional Requirements

- FR-001: ObservabilityLayer 在创建节点 span 时,span name 必须使用形如 `dify:<canonical-kind>` 的稳定字符串(canonical-kind 为内置节点类型对应的 kebab-case 标识),不再以用户自定义的 `node.title` 为 span name。LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点的 Span 由 util-genai helper(FR-009)发射,其原生 span name(如 `chat <model>` / `retrieval <data_source>` / `execute_tool <tool>` / `invoke_agent <agent>`)在 invocation finish 完成后由 ObservabilityLayer 调用 `span.update_name(f"dify:<kind>")` 改写为本期统一命名口径,原值通过 `gen_ai.original.span.name` attribute 保留。
- FR-002: 系统必须存在一个唯一的"内置节点类型 → canonical kind"映射,覆盖当前 `graphon.enums.BuiltinNodeTypes` 的全部 21 种内置节点(`START`、`END`、`ANSWER`、`LLM`、`KNOWLEDGE_RETRIEVAL`、`TOOL`、`AGENT`、`IF_ELSE`、`ITERATION`、`LOOP`、`CODE`、`TEMPLATE_TRANSFORM`、`HTTP_REQUEST`、`LIST_OPERATOR`、`VARIABLE_ASSIGNER`、`LEGACY_VARIABLE_AGGREGATOR`、`PARAMETER_EXTRACTOR`、`QUESTION_CLASSIFIER`、`DOCUMENT_EXTRACTOR`、`HUMAN_INPUT`、`DATASOURCE`)。FR-001 / FR-003 / FR-004 必须从该映射读取,禁止在多处复刻字符串常量。canonical kind 取值清单见 requirements.md 附录 A。
- FR-003: 节点 span 的 `gen_ai.span.kind` 必须按以下来源写入:LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点的 `gen_ai.span.kind` 由 util-genai helper 在 `stop_*` 时自动写入(取值为 util-genai 实际写出的常量,例如 `LLM` / `RETRIEVER` / `EXECUTE_TOOL` / `INVOKE_AGENT`,以 PR 评审环节实测为准并与 requirements.md 附录 A 对齐);其余工作流控制/工具节点的 `gen_ai.span.kind` 由 `DefaultNodeOTelParser` 显式写入 `WORKFLOW_<KIND>`(取值表与附录 A 一致),不再统一为 `TASK`。
- FR-004: 每一个节点 span 必须额外携带两个 attribute:`dify.node.kind`(canonical-kind,与 FR-001 中 span name 的后缀一致)与 `dify.node.title`(原 `node.title` 字符串,可能为空)。原有 `node.type` attribute 保留并写入 canonical-kind,使旧查询可继续命中。
- FR-005: 当 `node.node_type` 不在 FR-002 映射中(自定义/未知节点)时,系统必须降级处理:走全局 `tracer.start_span` 直发 Span,span name 落为 `dify:custom`,`dify.node.kind=custom`,`gen_ai.span.kind=TASK`(与历史一致),并把原始 `node_type` 字符串透出到 `dify.node.kind.raw`;不抛异常、不丢链路、不写出非 ASCII span name。该降级路径不依赖 util-genai。
- FR-006: 当 util-genai helper 在 `start_*` / `stop_*` / `fail_*` 阶段抛出任意异常时,`ObservabilityLayer` 必须捕获并降级到 `tracer.start_span` 直发 Span,降级路径仍写出 `dify.node.kind` / `dify.node.title` / `node.type` / `gen_ai.framework=dify` / `gen_ai.span.kind`(LLM 节点降级为 `LLM`、Retrieval 为 `RETRIEVER`、Tool 为 `TOOL`、Agent 为 `TASK`、其余按 FR-003);降级 warning 在单次进程生命周期内对同一 raw `node_type` 仅记录一次。降级行为不得阻断工作流执行,不得影响其他节点的 Span 发射。
- FR-007: 现有 `gen_ai.framework=dify`、`node.type`、`node.id`、`node.execution_id`、`input.value`、`output.value` 等 attribute 的写入条件、内容门控行为(`should_include_content()`)、错误处理路径(`record_exception` + `StatusCode.ERROR`)不得发生回归。LLM / Retrieval / Tool / Agent 节点的 `gen_ai.*` 专属 attribute 经 invocation 字段写入(FR-009 ②),取值与改造前由 `LLMNodeOTelParser` / `RetrievalNodeOTelParser` / `ToolNodeOTelParser` 直接 `set_attribute` 的取值等价。EE/CE 内容门控在 invocation 字段赋值前判定:门控为 False 时不写入 `input_messages` / `output_messages` / `query` / `documents` / `tool_call_arguments` / `tool_call_result` 等正文字段。
- FR-008: 文档(`docs/` 中已有的可观测性相关页面,如不存在则新增一份 changelog/迁移说明)需更新,记录 (a) canonical kind 取值集合;(b) 新 attribute 字典;(c) 新版 `gen_ai.span.kind` 取值集合(`WORKFLOW_*` + util-genai 写出的 `LLM` / `RETRIEVER` / `EXECUTE_TOOL` / `INVOKE_AGENT`);(d) 对下游"`gen_ai.span.kind=TASK` 等值匹配 → 取值集合匹配"的一次性迁移指引;(e) `aliyun_trace` provider 暂未跟进的过渡期提示;(f) 历史 `gen_ai.span.kind=TOOL` / `TASK` 与 util-genai `EXECUTE_TOOL` / `INVOKE_AGENT` 的命名差异提示;(g) `loongsuite-util-genai` 引入与可调环境变量(`OTEL_SEMCONV_STABILITY_OPT_IN`、`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`)。本期不提供运行期开关回退,迁移须在文档发布后一次性完成。
- FR-009: util-genai Span 发射改造 — `loongsuite-util-genai` 必须以**运行期依赖**形式加入 `api/pyproject.toml` 并按 lockfile 流程更新 `uv.lock`。本期使用必须满足:① **依赖与导入**:导入命名空间 `opentelemetry.util.genai`;`import` 收敛在 `api/extensions/otel/`(包括 `semconv/` 与新增的 handler getter 模块)与 `api/core/app/workflow/layers/observability.py`,其余模块通过 `extensions.otel.semconv` / `extensions.otel.runtime` 暴露的常量符号或 handler getter 间接引用,禁止直接从 `opentelemetry.util.genai...` 在工作流节点实现内导入。② **Span 发射**:LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点的 Span 必须经 `ExtendedTelemetryHandler.start_*` / `stop_*` / `fail_*`(或对应上下文管理器)产生与终结;`extensions/otel/parser/{llm,retrieval,tool}.py` 的 parse 协议由"在外部 Span 上 `set_attribute`"改造为"构造对应 invocation dataclass(`LLMInvocation` / `RetrievalInvocation` / `ExecuteToolInvocation` / `InvokeAgentInvocation`)";`ObservabilityLayer.on_node_run_start` / `on_node_run_end` 持有节点 → invocation 映射并代为启停。③ **handler 单例**:通过新增 `extensions.otel.runtime`(或 `genai_handler.py`)中的懒加载 getter 访问 `get_extended_telemetry_handler()` 返回的单例,不传 `tracer_provider` / `logger_provider`(沿用全局 Provider);该 getter 必须在 `ENABLE_OTEL=False` 时返回 `None` 或被 `_is_disabled` 短路。④ **常量复用**:若 util-genai 已提供本期所需的 `gen_ai.span.kind` 常量(`LLM` / `RETRIEVER` / `EXECUTE_TOOL` / `INVOKE_AGENT` / `TASK`)或 attribute 键名(`gen_ai.request.model` / `gen_ai.usage.input_tokens` 等),直接 `import`;暂未提供的(`WORKFLOW_*` 取值)在 `extensions/otel/semconv/node_kind.py` 维护本地 fallback 并加注"待 util-genai 提供后切换"。⑤ **不替换全局 TracerProvider**:不构造 `TracerProvider` / `MeterProvider` / `LoggerProvider`,仍由 `api/extensions/ext_otel.py` 注册的全局 Provider 提供。
- FR-010: E2E 部署与 stdout 验证 — 仓库 `dev/k8s-e2e/dify-system/` 子树下必须提供:(a) 一组 K8s manifest(API Deployment + Worker Deployment + Service + ConfigMap;DB / Redis / Vector store 走 `dify-system` 命名空间内已有的依赖,无则用轻量内嵌镜像);(b) 一份 `Makefile` 目标 `make e2e-dify-otel-console`,该目标必须:① 通过 `KUBECONFIG=~/.kube/config-hk` 把改造后的 Dify 镜像部署到 `dify-system` 命名空间;② 通过 ConfigMap / env 启用 OTel(`ENABLE_OTEL=True`,`OTEL_EXPORTER_TYPE=console`,`OTEL_SAMPLING_RATE=1.0`);③ 等待 Pod Ready 后运行一个预置混合节点工作流(`start → if-else → code → http-request → llm → end`,LLM 可走本地 mock 或 `LLMInvocation` 字段缺省);④ 抓取 `kubectl logs` 中的 Span 行,断言至少出现 `dify:start` / `dify:if-else` / `dify:code` / `dify:http-request` / `dify:llm` / `dify:end`,且 `gen_ai.span.kind` 集合落在 requirements.md 附录 A 的合法取值集合内;⑤ 用例可重复执行,部署清理由 Makefile 兜底(`kubectl delete -k ...`)。本 E2E 不接入 ARMS / OTLP collector,stdout 即唯一断言来源。该目标在 CI 中默认 skip(`@pytest.mark.e2e` 或 Makefile 单独目标),实施 Agent 在合并前手工执行一次,日志归档到实施 PR 描述。

## 6. Constraints

- CS-001: 实现仅允许修改 `api/` 子树下与 OTel 上报相关的代码与测试,以及新增 `dev/k8s-e2e/dify-system/` 子树。关键改动文件:`api/extensions/otel/parser/{base,llm,retrieval,tool,agent}.py`(新增 `agent.py`)、`api/extensions/otel/parser/__init__.py`、`api/extensions/otel/semconv/{gen_ai,dify,node_kind}.py`(新增 `node_kind.py`)、`api/extensions/otel/runtime.py`(新增 `get_handler()` 单例,或新增 `genai_handler.py`)、`api/core/app/workflow/layers/observability.py`、`api/pyproject.toml` + `uv.lock`、相应 `api/tests/...`、`dev/k8s-e2e/dify-system/**`、`Makefile`(`e2e-dify-otel-console` 目标)、`docs/.../*.mdx`(若存在)。**本期不得改动** Dify 前端代码、其他 trace provider 代码、`api/providers/trace/trace-aliyun/**`(整目录)、`api/configs/observability/otel/otel_config.py`。
- CS-002: 依赖与导入约束 — 新增 `loongsuite-util-genai` 至 `api/pyproject.toml`(版本约束在实施 PR 中按当时最新稳定版固定,并随之刷新 `uv.lock`),不再引入其它新的 Python 运行期依赖,前端锁文件 `pnpm-lock.yaml` / `pnpm-workspace.yaml` 不动。该包(导入命名空间 `opentelemetry.util.genai`)的 `import` 路径必须收敛在 `api/extensions/otel/**` 与 `api/core/app/workflow/layers/observability.py`;`extensions/otel/parser/{llm,retrieval,tool,agent}.py` 可直接 `import` invocation dataclass(`LLMInvocation` / `RetrievalInvocation` / `ExecuteToolInvocation` / `InvokeAgentInvocation` / `InputMessage` / `OutputMessage` / `Text` / `RetrievalDocument` 等);`ExtendedTelemetryHandler` 的获取必须经由 `extensions.otel.runtime`(或 `genai_handler.py`)的单例 getter,禁止在 `parser/*` 内直接调用 `get_extended_telemetry_handler()`。`graphon.nodes.*` / `core/app/workflow/**` 之外的业务模块禁止直接 `import opentelemetry.util.genai`(可通过 `extensions.otel.*` 中转)。
- CS-003: 兼容性约束 — 任何已订阅 `gen_ai.framework=dify`、`node.type=<kind>`、`gen_ai.span.kind=LLM/RETRIEVER` 的下游必须不需要改造。`gen_ai.span.kind` 取值集合扩展属于增量变化,禁止删除 `LLM` / `RETRIEVER` 既有取值;历史 `TOOL` / `TASK` 在 LLM/Retriever/非 Tool/非 Agent 节点上的等值匹配规则将在文档发布后由消费方一次性迁移到取值集合匹配,本期不提供运行期回退。util-genai 写出的 `EXECUTE_TOOL`(原历史 `TOOL`)/ `INVOKE_AGENT`(原历史 `TASK`)的命名差异由 FR-008 文档显式列入迁移指引。OTel SpanKind 的升级(LLM → CLIENT、Retrieval → RETRIEVER)由 FR-008 文档明示,不视为破坏性变化。
- CS-004: 性能约束 — 新增逻辑需为常量时间字符串映射 + invocation dataclass 构造,不得在节点路径上引入新的 IO、锁或反射;util-genai helper 内部走与 `tracer.start_span` 同源的 OTel SDK,不得引入额外网络/IO。`ObservabilityLayer` 既有 `try/except` 容错路径不得收紧,任何新引入的异常都必须吞掉并以单次 `logger.warning` 记录(包括 util-genai 抛错 → FR-006 降级路径),绝不传播至工作流执行栈。
- CS-005: 隐私与内容门控 — 新写入的 `dify.node.title` 是节点元数据,不被视为 EE 内容,`should_include_content()` 不门控本字段;span name 必须保持 ASCII 安全,不得直接拼入 `node.title` 中可能的中文/Emoji 字符。invocation 字段写入前判定 `should_include_content()`,门控为 False 时不写入 `input_messages` / `output_messages` / `query` / `documents` / `tool_call_arguments` / `tool_call_result` 等正文字段;`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 由 util-genai 自身门控 attribute 写出层,Dify 侧不重复实现。
- CS-006: 命名空间约束 — 所有新 attribute 必须落在 `dify.*` 或 `gen_ai.*` 现有命名空间内,不得新建顶级命名空间;`gen_ai.span.kind` 新增取值必须为大写蛇形,canonical kind 必须为小写 kebab-case,两者一一对应,不得出现别名。util-genai 写出的常量字符串若与 requirements.md 附录 A 不一致,以 util-genai 为准并同步修订附录 A,实施 PR 评审环节最终确认。
- CS-007: 测试约束 — 现有 `api/tests/unit_tests/core/workflow/graph_engine/layers/test_observability.py` 中对 `spans[0].name == mock_llm_node.title` 的断言必须随 FR-001 同步更新;新增映射表必须有参数化用例覆盖全部 21 种 BuiltinNodeTypes + 1 种自定义兜底;新增 invocation 字段映射用例(LLM / Retrieval / Tool / Agent 四类);新增 util-genai helper 抛错降级路径用例(FR-006);新增 E2E 测试目标(FR-010,默认 skip,合并前手工跑)。`api/providers/trace/trace-aliyun/tests/**` 本期不动。
- CS-008: 假设约束 — 假设 `graphon~=0.4.0` 在本期内 `BuiltinNodeTypes` 取值不变;若 graphon 发布带新枚举值的版本,FR-005 自定义兜底必须保证不破坏链路。假设 `loongsuite-util-genai` 的 `ExtendedTelemetryHandler` API(`llm()` / `retrieval()` / `execute_tool()` / `invoke_agent()` 上下文管理器与 invocation dataclass 字段)在实施 PR 期间稳定;若上游变更签名,实施 Agent 在 PR 中同步本规格附录 C 与 invocation 字段映射,必要时回退到 `tracer.start_span` 直发 + 语义常量的方案,FR-001 / FR-003 / FR-004 / FR-007 / FR-008 / FR-010 不受影响,FR-009 由实施 PR 评审决定具体落地形态。
- CS-009: E2E 集群约束 — `~/.kube/config-hk` 集群与 `dify-system` 命名空间在本期默认可用,但 E2E manifest 与 Makefile 必须满足:① 不依赖集群内已存在的临时资源,所需依赖(DB / Redis / Vector store)要么用 `dify-system` 内已稳态部署的服务,要么由 manifest 自带轻量镜像;② 部署使用 `apply -k` 或等价 idempotent 命令,可重复运行;③ 默认镜像走开发镜像仓(`registry.cn-hongkong.aliyuncs.com/...` 或等价),如未配置则在 PR 描述中说明本地加载方式;④ E2E 流程不消耗外部 LLM 配额,LLM 节点用 mock provider 或 `gen_ai.request.model="mock"` 走 LLMInvocation 字段缺省。

## 7. Acceptance Criteria

- [ ] AC-001: 覆盖 FR-001 / FR-002 / FR-009 ②。工作流执行包含 21 种 BuiltinNodeTypes 中任意节点时,该节点对应的 OTel span name 等于 `dify:<canonical-kind>`(canonical-kind 取值与 requirements.md 附录 A 完全一致),不再依赖任何运行期开关。LLM / KNOWLEDGE_RETRIEVAL / TOOL / AGENT 节点的 Span 由 util-genai helper 启停(可通过 `start_llm` / `start_retrieval` / `start_execute_tool` / `start_invoke_agent` 的 mock 或 spy 验证调用关系),其原生 span name 在 `on_node_run_end` 完成后被改写为 `dify:<canonical-kind>`,原值通过 `gen_ai.original.span.name` attribute 保留。
- [ ] AC-002: 覆盖 FR-003 / FR-009 ④。同一工作流执行中:LLM 节点的 `gen_ai.span.kind` 等于 util-genai helper 实际写入的常量(预期为 `LLM`);Retrieval 等于 `RETRIEVER`;Tool 等于 `EXECUTE_TOOL`;Agent 等于 `INVOKE_AGENT`(若 util-genai 实测写出其它常量,以实测为准并同步附录 A);其余节点的 `gen_ai.span.kind` 取值落在 requirements.md 附录 A 定义的 `WORKFLOW_*` 集合内,不再出现塌缩为 `TASK` 的情况(自定义兜底分支除外)。
- [ ] AC-003: 覆盖 FR-004。任意节点 span 上同时存在 `dify.node.kind`(canonical-kind 字符串)与 `dify.node.title`(等于 `node.title`,允许为空字符串);`node.type` attribute 同样写入 canonical-kind,与 `dify.node.kind` 取值一致。
- [ ] AC-004: 覆盖 FR-005。当输入一个 `node.node_type` 不在 FR-002 映射表中的自定义节点时,产出的 span 经全局 `tracer.start_span` 直发(不经 util-genai),span name 为 `dify:custom`、`dify.node.kind=custom`、`gen_ai.span.kind=TASK`、`dify.node.kind.raw=<原始 node_type 字符串>`,工作流执行不抛异常,链路不丢失。
- [ ] AC-005: 覆盖 FR-006 / CS-004。当 util-genai helper 在 `start_*` / `stop_*` / `fail_*` 阶段抛出任意异常(测试中通过 monkey-patch 注入),`ObservabilityLayer` 必须降级到 `tracer.start_span` 直发 Span,降级路径仍写出 `dify.node.kind` / `dify.node.title` / `node.type` / `gen_ai.framework=dify` / `gen_ai.span.kind`(LLM 节点降级为 `LLM`、Retrieval 为 `RETRIEVER`、Tool 为 `TOOL`、Agent 为 `TASK`、其余按 FR-003);`logger.warning` 在单次进程生命周期内对同一 raw `node_type` 仅记录一次;工作流执行不被阻断。
- [ ] AC-006: 覆盖 FR-007 / FR-009 ② / CS-005。在 AC-001~AC-005 的所有断言下,`gen_ai.framework=dify`、`node.id`、`node.execution_id`、错误状态(`StatusCode.ERROR` + `record_exception`)、`should_include_content()` 控制下的 `input.value` / `output.value` 行为与改动前完全一致;LLM / Retrieval / Tool 节点的 `gen_ai.request.model` / `gen_ai.provider.name` / `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `gen_ai.usage.total_tokens` / `gen_ai.input.messages` / `gen_ai.output.messages` / `gen_ai.tool.name` / `gen_ai.tool.type` / `retrieval.query` 等 attribute 的取值与改造前由 `LLMNodeOTelParser` / `RetrievalNodeOTelParser` / `ToolNodeOTelParser` 直接 `set_attribute` 的取值等价(允许键名因 util-genai 升级而出现别名,但取值集合一致);`api/configs/observability/otel/otel_config.py` 本期 diff 中没有新增控制本特性的开关字段。
- [ ] AC-007: 覆盖 FR-008 / GL-002。文档(可观测性相关页面或迁移说明)中明确列出 canonical kind 全集、新 attribute 字典、`gen_ai.span.kind` 新取值集合(包含 util-genai 写出的 `EXECUTE_TOOL` / `INVOKE_AGENT` 与本仓库 `WORKFLOW_*`)、对下游"TASK 等值匹配 → 取值集合匹配"的一次性迁移指引、`aliyun_trace` 暂未跟进的过渡期提示、`OTEL_SEMCONV_STABILITY_OPT_IN` / `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 配置说明;PR 描述中包含面向下游(护栏规则、看板)的迁移指引,并明确说明本期不提供运行期回退开关。
- [ ] AC-008: 覆盖 CS-004。在与 baseline 相同负载下执行包含 50 个混合类型节点的工作流时,新增映射逻辑与 invocation 构造不引入额外日志风暴(自定义兜底 / util-genai 降级 warning 在单次进程生命周期内对同一 raw `node_type` 仅记录一次),工作流端到端耗时无可观测回归(本地 micro-bench 比对差异 < 5%)。
- [ ] AC-009: 覆盖 FR-009 ① / CS-002 / Out of Scope。`api/pyproject.toml` 与 `uv.lock` 必须出现 `loongsuite-util-genai` 新增条目(且无其它新增 Python 运行期依赖);`pnpm-lock.yaml` / `pnpm-workspace.yaml` 无 diff;对该包的 `import` 语句仅出现在 `api/extensions/otel/**` 与 `api/core/app/workflow/layers/observability.py`(可通过 `grep -R "opentelemetry.util.genai" api/` 验证);其余模块通过 `extensions.otel.*` 中转。`api/providers/trace/trace-aliyun/**` 与 arize-phoenix / langfuse / langsmith / tencent / weave 目录下源码与测试 0 diff;`api/configs/observability/otel/otel_config.py` 0 diff。
- [ ] AC-010: 覆盖 FR-010 / GL-004。在 `~/.kube/config-hk` 集群 `dify-system` 命名空间执行 `make e2e-dify-otel-console` 后:① Dify API + Worker Pod Ready;② 预置混合节点工作流(`start → if-else → code → http-request → llm → end`)成功执行至少一次;③ `kubectl logs` 输出的 Span 行集合中至少包含 `dify:start` / `dify:if-else` / `dify:code` / `dify:http-request` / `dify:llm` / `dify:end`;④ `gen_ai.span.kind` 取值集合 ⊆ requirements.md 附录 A 合法集合;⑤ 测试用例可重复执行,Makefile 自带清理(`kubectl delete -k ...`);⑥ 实施 PR 描述包含一次实跑 stdout 摘要与 Makefile 命令日志。本期不接入 ARMS / OTLP collector。

## 8. Test Plan

| 测试类型 | 覆盖项 | 验证方式 | 负责人 |
| --- | --- | --- | --- |
| 单元测试 | AC-001 / AC-002 / AC-003 / FR-001~FR-004 / FR-009 ② | 在 `api/tests/unit_tests/core/workflow/graph_engine/layers/test_observability.py` 中扩展参数化用例,以 mock node + `InMemorySpanExporter` + util-genai handler spy(或 fake `ExtendedTelemetryHandler`)校验 21 种 BuiltinNodeTypes 的 span name、`gen_ai.span.kind`、`dify.node.kind` / `dify.node.title` / `node.type`,以及 LLM / Retrieval / Tool / Agent 节点的 `start_*` / `stop_*` 调用关系与 `span.update_name(...)` 的执行;同步更新原 `assert spans[0].name == mock_llm_node.title` 断言。 | 实施 Agent |
| 单元测试 | AC-004 / FR-005 | 新增用例,以一个伪造的非内置 `node_type` 触发兜底分支,断言走 `tracer.start_span` 直发(不经 util-genai)、span name=`dify:custom`、`dify.node.kind.raw` 等于伪造原值、无异常抛出。 | 实施 Agent |
| 单元测试 | AC-005 / FR-006 | monkey-patch util-genai handler 的 `start_llm` / `stop_llm` / `fail_llm` 抛 `RuntimeError`,断言 `ObservabilityLayer` 降级到 `tracer.start_span`、Span 仍写出完整 attribute、`logger.warning` 仅记录一次、工作流不被阻断。其余三类节点(retrieval/tool/agent)类似断言。 | 实施 Agent |
| 单元测试 | AC-006 / FR-007 / CS-005 | 新增/复用错误路径与内容门控用例,验证 `record_exception`、`StatusCode.ERROR`、`input.value` / `output.value` 在 EE 与 CE 模式下行为不变;断言 `should_include_content()=False` 时 `LLMInvocation.input_messages` / `output_messages` 等正文字段未被写入,`should_include_content()=True` 时与现状取值等价;断言 `otel_config.py` 中没有新增控制本特性的开关字段。 | 实施 Agent |
| 单元测试 | CS-007 / FR-002 | 引入参数化测试用例,显式以 21 种 BuiltinNodeTypes + 1 种自定义共 22 组输入参数化,保证未来枚举增减时测试集自然失败提醒补齐。 | 实施 Agent |
| 集成测试 | AC-001~AC-006 | 复用 `api/tests/integration_tests/workflow/...` 现有 workflow 流程,跑一个混合节点(含 `if-else` / `code` / `http-request` / `tool` / `llm`)的工作流,把 OTel exporter 切到 `InMemorySpanExporter`,断言落盘 span 的命名集合、`gen_ai.span.kind` 集合、`dify.node.*` 集合、`gen_ai.*` LLM/Tool/Retrieval 字段集合与改造前等价。 | 实施 Agent |
| 回归测试 | AC-006 / AC-008 / AC-009 | 在 CI 中运行 `api/tests` 完整套件,确认 `gen_ai.framework`、`node.type`、`node.execution_id`、错误路径、内容门控相关历史用例 0 失败;`api/pyproject.toml` 与 `uv.lock` 仅新增 `loongsuite-util-genai` 一条依赖,`pnpm-lock.yaml` / `pnpm-workspace.yaml` 无 diff;`grep -R "opentelemetry.util.genai" api/` 命中范围仅落在 `api/extensions/otel/**` 与 `api/core/app/workflow/layers/observability.py`;`api/providers/trace/trace-aliyun/**` 与 `api/providers/trace/{arize-phoenix,langfuse,langsmith,tencent,weave}` 目录无文件 diff;`api/configs/observability/otel/otel_config.py` 无文件 diff;额外执行一次 50 节点混合工作流的本地 micro-bench(基于 `pytest-benchmark` 或等价工具,对比 baseline,断言耗时差异 < 5% 以覆盖 AC-008)。 | 实施 Agent |
| E2E 测试 | AC-010 / FR-010 / GL-004 | 在 `~/.kube/config-hk` 集群 `dify-system` 命名空间执行 `make e2e-dify-otel-console`(默认 skip,合并前手工跑一次):部署改造后镜像 → 等待 Pod Ready → 触发预置混合节点工作流 → 抓取 `kubectl logs` → 断言 Span 行集合 ⊇ {`dify:start`, `dify:if-else`, `dify:code`, `dify:http-request`, `dify:llm`, `dify:end`} 且 `gen_ai.span.kind` 取值集合落入附录 A 合法集合;归档 stdout 摘要到 PR 描述。完成后 `make e2e-dify-otel-console-clean`(或等价目标)清理资源。 | 实施 Agent |
| 手工验证 | AC-001 / AC-002 / AC-007 | 在本地或预发起一个包含至少 5 种节点的工作流,接 ARMS 模拟 endpoint,核对 ARMS UI 中 span name、`dify.node.kind`、`gen_ai.span.kind` 显示;按文档迁移指引在 ARMS 上完成一次"TASK 等值匹配 → 取值集合匹配"的查询规则迁移演练,验证迁移后命中范围符合预期;核对文档中 `aliyun_trace` 暂未跟进的过渡期提示是否清晰。 | 安全护栏团队 + 实施 Agent |

可选测试场景:

- 正常流程: 21 种 BuiltinNodeTypes 全覆盖参数化;典型混合工作流(`start → if-else → code → http-request → llm → end`)。
- 边界场景: `node.title` 为空字符串/中文/Emoji;`node.execution_id` 为空(span 不被创建,与现有逻辑一致);自定义节点(FR-005);util-genai helper 抛错(FR-006)。
- 异常场景: 节点抛 `ValueError` / `RuntimeError` 时 span 仍正确收尾,`record_exception` + `StatusCode.ERROR` 不受新增逻辑影响;`graphon` 升级带来未知枚举(模拟方式:测试中构造一个新的 `node_type` 字符串)。
- 兼容性场景: 旧下游按 `gen_ai.framework=dify` / `node.type=if-else` / `gen_ai.span.kind=LLM` / `RETRIEVER` 已建查询/告警继续命中;旧下游按 `gen_ai.span.kind=TASK` / `TOOL` 等值匹配的非 LLM/Retriever 节点查询规则按文档迁移指引一次性切换为取值集合匹配,完成后命中范围与改动前一致。
- E2E 场景: ACK `dify-system` 命名空间实跑、stdout 校验、可重复执行、清理回归。

## 9. Related Documents

- 需求文档: `spec/dify-workflow-span-naming/requirements.md`(本仓库)
- 工作项: [AGE-42](mention://issue/be600c9c-b2fb-4cba-81ff-7d0a3ddd3b0d) Dify工作流Span埋点优化
- 关键现状代码:
  - `~/dev_musi/dify/api/core/app/workflow/layers/observability.py`(span 生命周期与 parser 注册)
  - `~/dev_musi/dify/api/extensions/otel/parser/{base,llm,retrieval,tool}.py`(parser 协议;本期重构为 invocation 构造)
  - `~/dev_musi/dify/api/extensions/otel/semconv/{gen_ai,dify}.py`(attribute 常量;本期新增 `node_kind.py`)
  - `~/dev_musi/dify/api/extensions/otel/runtime.py`(全局 TracerProvider 设置;本期新增 handler 单例 getter)
  - `~/dev_musi/dify/api/extensions/ext_otel.py`(全局 Provider 注册,本期不改)
  - `~/dev_musi/dify/api/configs/observability/otel/otel_config.py`(本期不改)
  - `~/dev_musi/dify/api/tests/unit_tests/core/workflow/graph_engine/layers/test_observability.py`(line 63 断言需同步更新)
- 外部参考: [`opentelemetry-util-genai` (LoongSuite)](https://github.com/alibaba/loongsuite-python-agent/tree/main/util/opentelemetry-util-genai)(本期作为运行期 Span 发射器,详见 FR-009 / CS-002 与 requirements.md 附录 B、C)
- E2E 资产: `dev/k8s-e2e/dify-system/**` 与 `Makefile` 中 `e2e-dify-otel-console` 目标(本期新增)

## 10. Change Log

### CL-01 初版 spec

- 日期: 2026-05-18
- 变更人: AgentLoop Spec Agent
- 用户输入: 工作项 AGE-42 描述要求按节点类型细分 Dify 工作流 span,span name 形如 `dify:conditional-branch` / `dify:code-execution` / `dify:http-request`,保留原始节点类型作为 attribute,向后兼容,并参考 `opentelemetry-util-genai` 做语义对齐。
- 变更内容: 基于 `spec/dify-workflow-span-naming/requirements.md` 与对 `~/dev_musi/dify` 现状的代码勘察(`observability.py:109` 的 `node.title` span name、`base.py:107-115` 的 `TASK` 兜底、`aliyun_trace.py:303-341` 的服务端重建路径、`BuiltinNodeTypes` 21 种取值)首次创建本 Feature 规格。明确 4 项 Goal、9 项 FR、8 项 Constraint、10 项 AC、7 行测试矩阵,并给出 `OTEL_NODE_SPAN_DETAILED_NAMING` 灰度回滚开关与未来对接 `opentelemetry-util-genai` 的 Future Work 清单。

### CL-02 spec-reviewer 审阅修订

- 日期: 2026-05-18
- 变更人: AgentLoop Spec Agent (spec-reviewer)
- 用户输入: musi 通过 spec-writer 工作流要求审阅 spec.md。
- 变更内容: 直接修复:Meta 字段中作者标准化为 `AgentLoop Spec Agent`、状态由 `Draft` 切换为 `Reviewing`(进入评审窗口)。FR↔AC↔Test Plan 追溯矩阵全量通过。版本保持 `1.0.0`(本次修改为元数据/状态正常化)。

### CL-03 依赖策略调整(opentelemetry-util-genai 引入为依赖,仅作语义参考)

- 日期: 2026-05-18
- 变更人: AgentLoop Spec Agent
- 用户输入: musi 评论:"我希望 opentelemetry-util-genai 仅作为语义参考作为依赖进来。"
- 变更内容: 把"不引入 `opentelemetry-util-genai`"翻转为"引入为运行期依赖,但仅消费语义常量"。新增 FR-010 明确依赖范围、`import` 收敛位置、缺失常量的 fallback 策略;改写 CS-002 / AC-010 / Test Plan 同步;同步修订 `requirements.md`。版本由 `1.0.0` 升至 `1.1.0`,`requirements.md` 同步更新。

### CL-04 移除运行期灰度/回滚开关(默认即兼容)

- 日期: 2026-05-18
- 变更人: AgentLoop Spec Agent
- 用户输入: musi 评论:"然后我觉得这个兼容旧场景的开关是不需要的,默认就应该是兼容的,不需要开关控制。"
- 变更内容: 移除 `OTEL_NODE_SPAN_DETAILED_NAMING` 灰度/回滚开关。删除 GL-004 / FR-007 / AC-006(开关相关条目),后续 FR / AC 序号顺移。修订 CS-001 / CS-003 / FR-008 / AC-001 / AC-006 / AC-007 / Test Plan / Out of Scope / Context;同步修订 `requirements.md`。版本由 `1.1.0` 升至 `1.2.0`。

### CL-05 优先 ObservabilityLayer 路径 + util-genai 升为 Span 发射器 + 新增 K8s E2E

- 日期: 2026-05-18
- 变更人: AgentLoop Spec Agent
- 用户输入: musi 评论(三条建议):
  1. 优先实现 ObservabilityLayer 层的埋点,aliyun tracer 那边暂时不用管;
  2. opentelemetry-util-genai 不止作为语义参考,需要用它来产生 Span(TASK / LLM / RETRIEVER 等),需要查看 loongsuite 仓库中代码与使用方法,产出详细的使用与改造计划;
  3. 需要添加 E2E 测试,在 `~/.kube/config-hk` 对应集群创建 `dify-system` 命名空间,部署改造后的 Dify,开启 OTel 上报(可先上报到 console 标准输出验证)。
- 变更内容:
  1. **范围调整(对应建议 1)**:
     - GL-003 由"两条上报路径输出一致"改写为"以 LoongSuite util-genai 为运行期 Span 发射器";新增 GL-004(E2E 实跑验证)。
     - Out of Scope 增补"不在本期改造 `aliyun_trace` provider";Future Work 新增 FW-1(aliyun 同等改造排期)、FW-4(等 util-genai 提供通用 helper 后切换 `WORKFLOW_*` 节点)。
     - 删除原 FR-006(aliyun_trace 同步改造)与 AC-005(跨路径一致性);新 FR-006 改为"util-genai helper 抛错降级路径"(原 CS-004 容错路径独立成条),新 AC-005 改为对应降级路径验证。
     - CS-001 改动文件清单删除 `api/providers/trace/trace-aliyun/**`,并新增 `dev/k8s-e2e/dify-system/**` 与 `Makefile` 改动范围;Test Plan 删除原"AC-005/FR-006 aliyun 同步用例"行,加入 E2E 行;手工验证段补充"`aliyun_trace` 暂未跟进的过渡期提示"核对项;Related Documents 删去 `aliyun_trace.py`,新增 E2E 资产指向。
  2. **Span 发射改造(对应建议 2)**:
     - 改写 FR-001 / FR-003 / FR-009(原 FR-009 的"语义常量参考"升级为"Span 发射器"),增加 invocation finish 后 `span.update_name(f"dify:<kind>")` 与 `gen_ai.original.span.name` 保留;新增 FR-007 中"LLM/Retrieval/Tool/Agent 节点专属 attribute 经 invocation 字段写入,与改造前等价"语义。
     - 新增 FR-006 util-genai helper 抛错降级路径;CS-002 改写"`import` 收敛"边界由 `extensions/otel/semconv/` 扩到 `extensions/otel/**` 与 `observability.py`,并新增 handler 单例 getter 强制走 `extensions.otel.runtime`(或 `genai_handler.py`);CS-008 增补"util-genai API 稳定性假设 + 上游变签名时回退方案"。
     - 在 requirements.md 补全附录 B(util-genai 关键 API 摘要)与附录 C(节点级使用与改造计划),覆盖 21 种 BuiltinNodeTypes 的发射通道与 invocation 字段映射;附录 A 表格新增"`gen_ai.span.kind`(util-genai 写入或本仓库写入)"列,标记 `EXECUTE_TOOL` / `INVOKE_AGENT` 与历史值的命名差异。FR-008 文档项纳入此命名差异迁移指引与 `OTEL_SEMCONV_STABILITY_OPT_IN` / `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 配置说明。
     - PyPI 包名由 `opentelemetry-util-genai`(社区上游)切换为 `loongsuite-util-genai`(LoongSuite 发行版,提供 retrieval / execute_tool / invoke_agent helper),导入命名空间仍为 `opentelemetry.util.genai`,详细取舍记入 requirements.md 附录 B。
  3. **E2E 通道(对应建议 3)**:
     - 新增 FR-010(E2E 部署 + stdout 验证)、AC-010(实跑断言);Test Plan 增补 E2E 行;CS-009 增补 E2E 集群约束;Related Documents 增补 E2E 资产路径。
  4. **元数据**:版本由 `1.2.0` 升至 `1.3.0`,Goal / FR / Constraint / AC / Test Plan / Out of Scope / Future Work / requirements.md(附录 A/B/C 与正文)同步更新。状态保持 `Reviewing`,等待重新审阅与最终批准。

### CL-06 spec-reviewer 审阅修订(v1.3.0 通过审阅)

- 日期: 2026-05-18
- 变更人: AgentLoop Spec Agent (spec-reviewer)
- 用户输入: 由 spec-writer 工作流自动触发,对 v1.3.0 做"标准合规 + FR/AC/Test 追溯矩阵"复核。
- 变更内容: 仅一处文字补全 — 回归测试行原本以"运行 `api/tests` 完整套件"覆盖 AC-008,但未显式提及 AC-008 中"50 节点混合工作流 micro-bench 差异 < 5%"的验证方式;补充明确 micro-bench 工具与断言阈值。`requirements.md` 未修改。版本保持 `1.3.0`(Patch 级文字澄清,无行为性变更)。FR-001~FR-010 全部有 AC 覆盖,所有 AC 在 Test Plan 中有验证方式;requirements.md 与 spec.md 表述一致。结论 **Passed**。
