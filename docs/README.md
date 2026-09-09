# ProjectLens 文档地图

**产品与研发共用总纲**：[`projectlens-product-document.md`](projectlens-product-document.md)

文档分层如下。冲突时以产品总纲为准。

## 现行文档

| 文档 | 用途 |
|------|------|
| [`projectlens-product-document.md`](projectlens-product-document.md) | 产品定义、目标/非目标、概念、MVP、路线图 |
| [`feishu-test-quickstart.md`](feishu-test-quickstart.md) | **新人飞书测试**：要配什么、怎么配、怎么冒烟 |
| [`../config/hermes/README.md`](../config/hermes/README.md) | Hermes `projectlens-safe` profile 拷贝说明 |
| [`feishu-setup.md`](feishu-setup.md) | 飞书应用、事件回调、权限与联调检查 |
| [`pilot-launch-config.md`](pilot-launch-config.md) | `.env` 配置剖面（Stub / Safe Live / 测试群） |
| [`projectspace-onboarding-guide.md`](projectspace-onboarding-guide.md) | 新项目 ProjectSpace 接入 |
| [`projectlens-tool-envelope.md`](projectlens-tool-envelope.md) | 工具与回答信封契约（实现对照） |

## 目标架构

| 文档 | 用途 |
|------|------|
| [`enterprise-architecture-v2.md`](enterprise-architecture-v2.md) | 企业版目标架构（**目标 ≠ 现状**） |
| [`phase4-projectspace-pluggable.md`](phase4-projectspace-pluggable.md) | ProjectSpace 可插拔设计说明 |

## 规划与历史（勿当安装指南）

以下文件可能描述尚未落地能力或已被总纲覆盖的旧表述。阅读前请先看产品总纲。

| 文档 | 说明 |
|------|------|
| [`projectlens-pilot-product-blueprint.md`](projectlens-pilot-product-blueprint.md) | 试点产品蓝图（历史） |
| [`projectlens-mvp-requirements.md`](projectlens-mvp-requirements.md) | 早期 MVP 需求（历史） |
| [`projectlens-pilot-development-spec.md`](projectlens-pilot-development-spec.md) | 试点研发规格（历史） |
| [`projectlens-change-log.md`](projectlens-change-log.md) | 分阶段变更记录 |
| [`hermes-conversational-answer-experience-plan.md`](hermes-conversational-answer-experience-plan.md) | 对话体验规划 |
| [`hermes-context-snapshot-development-plan.md`](hermes-context-snapshot-development-plan.md) | 上下文快照规划 |
| [`projectlens-context-composer-development-plan.md`](projectlens-context-composer-development-plan.md) | Context Composer 规划 |
| [`projectlens-long-term-memory-development-plan.md`](projectlens-long-term-memory-development-plan.md) | 长期记忆规划 |

## 推荐阅读顺序

1. [`projectlens-product-document.md`](projectlens-product-document.md) — 理解产品边界
2. 仓库根目录 [`README.md`](../README.md) — 快速开始
3. [`feishu-test-quickstart.md`](feishu-test-quickstart.md) — 飞书群联调（新人优先）
4. [`pilot-launch-config.md`](pilot-launch-config.md) + [`feishu-setup.md`](feishu-setup.md) — 配置细表
5. [`projectspace-onboarding-guide.md`](projectspace-onboarding-guide.md) — 接入真实项目
