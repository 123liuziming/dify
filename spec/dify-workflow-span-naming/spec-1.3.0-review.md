# Spec Review: 1.3.0

## 1. Review Summary

- **Conclusion**: **Passed**(三处非阻塞建议见 §4)
- **Spec Type**: Feature
- **Version**: 1.3.0
- **Status**: Reviewing(待用户最终批准)

本轮评审来源:musi 在 [AGE-42](mention://issue/be600c9c-b2fb-4cba-81ff-7d0a3ddd3b0d) 评论 `2c63b0f1-9aa5-467d-9b5f-289236c80c00` 提出三条建议:① 优先实现 ObservabilityLayer 路径,aliyun_trace 暂缓;② util-genai 升级为运行期 Span 发射器,产出详细使用与改造计划;③ 新增 K8s E2E 测试通道(`~/.kube/config-hk` 集群、`dify-system` 命名空间、OTel `console` exporter)。spec-writer 已在 `requirements.md` 与 `spec.md` 中按 CL-05 全量落地;本审阅仅做合规性复核与一处文字补全。

## 2. Changes Applied

仅一处补全,不构成实质性内容变更:

1. **回归测试行 AC-008 验证方式补全**:原表述只写"运行 `api/tests` 完整套件"覆盖 AC-008,但 AC-008 包含"50 节点混合工作流 micro-bench 差异 < 5%"具体阈值。补充"额外执行一次 50 节点混合工作流的本地 micro-bench(基于 `pytest-benchmark` 或等价工具,对比 baseline,断言耗时差异 < 5%)",AC-008 验证手段闭环。
2. **Change Log 追加 CL-06**,记录本次 spec-reviewer 修订。

`spec.md` 版本保持 `1.3.0`(Patch 级文字澄清,无行为性变更),`更新时间` 维持 `2026-05-18`。`requirements.md` 未变更。

## 3. Quality Checklist

| Check | Result | Notes |
| --- | --- | --- |
| 类型正确(Feature/Bugfix 语义) | Pass | 类型 `Feature`,Spec ID `FEATURE-001`,符合"新增能力 / 行为变更"语义。 |
| Meta 完整,状态合法,日期格式正确 | Pass | 状态 `Reviewing`,作者 `AgentLoop Spec Agent`,日期 `2026-05-18`,版本 `1.3.0`。 |
| 没有模板注释残留 | Pass | spec.md 与 requirements.md 中无 `[TODO]` / `[占位]` / `<!-- -->` 等残留。 |
| `TBD` 均有原因 | Pass | spec.md 内无 `TBD` 占位项;FR-009 ④ / CS-008 中"util-genai 写出常量以实测为准"属 PR 评审环节最终确认条款,非 TBD。 |
| 文档描述行为与验收,不写低层技术设计 | Pass(有意例外) | spec.md FR-009 与 requirements.md 附录 C 含较密的实施步骤映射,这是用户在评论 ② 中明确要求"产出详细的使用与改造计划"的直接交付物,故按用户范围保留;附录 C 显式标注"不进入 spec 验收范围,仅作过程性指引",未污染 AC。 |
| 需求 / 目标 / 约束能追溯到 requirements.md | Pass | requirements.md G1~G5 / N1~N7 / F1~F10 与 spec.md GL-001~004 / Out of Scope / FR-001~010 一一对齐,见 §5。 |
| Out of Scope 明确 | Pass | 7 条显式排除项 + Future Work(FW-1~4)。新增 FW-1(aliyun_trace 同等改造)、FW-4(util-genai 通用 helper 后续切换)。 |
| 每个重要 FR 有 AC 覆盖 | Pass | 见 §5 追溯矩阵。 |
| 每个 AC 在 Test Plan 中有验证方式 | Pass | 本轮补全 AC-008 micro-bench 验证手段后,AC-001~AC-010 全量闭环。 |
| Related Documents 只放引用 | Pass | 仅链接路径,无大段引用内容。 |
| Change Log 包含本次修改记录 | Pass | CL-01~CL-06 完整,本次审阅落地为 CL-06。 |
| 更新时保留已有编号和历史 Change Log | Pass | CL-01~CL-05 未改动;FR / AC / CS 编号在 CL-05 范围内有序号顺移(已在 CL-05 内说明),本轮未再次重排。 |

## 4. Residual Issues(非阻塞)

1. **`gen_ai.span.kind` 实测取值待 PR 阶段最终对齐**:FR-003 / AC-002 / requirements.md 附录 A / 附录 B 都给出"util-genai 写出 `LLM` / `RETRIEVER` / `EXECUTE_TOOL` / `INVOKE_AGENT`"的预期值,并明示"以 PR 评审环节实测为准"。这是 util-genai 升级为 Span 发射器后的合理 tie-breaker,不阻塞本期 spec 批准;但实施 Agent 在打开 PR 时必须把实测取值列入 PR 描述,与安全护栏团队对齐看板/告警迁移规则。
2. **PyPI 包名选型(`loongsuite-util-genai` vs `opentelemetry-util-genai`)**:本期选 `loongsuite-util-genai`(理由:仅它提供 retrieval / execute_tool / invoke_agent helper;详见 requirements.md 附录 B)。若社区上游 `opentelemetry-util-genai` 在实施期间补齐对应 helper,实施 Agent 可在 PR 中切换包名并同步本规格附录 B / FR-009 — 此切换属 Patch 级修订,不需要本期重新评审。
3. **E2E 镜像通道未最终敲定**:CS-009 ③ 给出"开发镜像仓 / 本地加载"两条候选,具体由实施 Agent 在 PR 中说明。该选择不影响 AC-010 的可观察性断言(`kubectl logs` 抓取 Span),但建议在打开 PR 时把镜像仓地址 / 本地加载脚本路径作为附件提交,便于后续 reviewer 复跑。

## 5. Traceability

### Requirement → Goal/FR 映射

| requirements.md | spec.md |
| --- | --- |
| G1(节点级 span 可识别) | GL-001 |
| G2(保留 node.type attribute) | GL-002 |
| G3(向后兼容,看板不破坏) | GL-002 / FR-007 / CS-003 |
| G4(util-genai 为运行期 Span 发射器) | GL-003 / FR-009 |
| G5(E2E 实跑验证) | GL-004 / FR-010 |
| N1(aliyun_trace 不在本期改造) | Out of Scope §4 / FW-1 |
| N6(无运行期开关) | Out of Scope §4 / CS-001 / FR-007 |
| N7(不替换全局 TracerProvider) | Out of Scope §4 / FR-009 ⑤ |
| F1~F10 | FR-001~FR-010(序号一一对应) |

### FR → AC → Test Plan 矩阵

| FR | AC | Test Plan 行 |
| --- | --- | --- |
| FR-001(span name `dify:<kind>` + `update_name` 改写) | AC-001 | 单元(行 1)+ 集成 |
| FR-002(canonical kind 唯一映射) | AC-001 | 单元(行 1 + 行 5 参数化) |
| FR-003(`gen_ai.span.kind` 细分) | AC-002 | 单元(行 1)+ 集成 |
| FR-004(`dify.node.kind` / `dify.node.title` / `node.type`) | AC-003 | 单元(行 1)+ 集成 |
| FR-005(自定义/未知节点兜底) | AC-004 | 单元(行 2) |
| FR-006(util-genai 抛错降级) | AC-005 | 单元(行 3) |
| FR-007(EE/CE 内容门控、错误路径不回归) | AC-006 | 单元(行 4)+ 集成 + 回归 |
| FR-008(文档与下游迁移指引) | AC-007 | 手工验证 |
| FR-009(依赖与导入约束 + Span 发射器改造) | AC-001 / AC-002 / AC-006 / AC-009 | 单元(行 1)+ 集成 + 回归 |
| FR-010(E2E 部署 + stdout 验证) | AC-010 | E2E |
| (CS-004 性能) | AC-008 | 回归(本轮补全 micro-bench 阈值) |

### Goal → AC 全覆盖

- GL-001 → AC-001 / AC-002 / AC-003 / AC-004
- GL-002 → AC-006 / AC-007
- GL-003 → AC-001 / AC-002 / AC-005 / AC-006 / AC-009
- GL-004 → AC-010

未发现孤立 AC(每个 AC 都至少覆盖一个 FR)或孤立 FR(每个 FR 都被至少一个 AC 验收)。

---

**Review 结论**:`spec.md` v1.3.0 通过 spec-reviewer 审阅,可提交用户最终批准。批准后将状态切换为 `Approved`、工作项 [AGE-42](mention://issue/be600c9c-b2fb-4cba-81ff-7d0a3ddd3b0d) 流转为 `in_review`。
