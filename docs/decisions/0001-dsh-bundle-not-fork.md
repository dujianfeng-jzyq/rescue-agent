# 0001 以 bundle 扩展 DSH，不 fork

状态：采纳

## 背景

方案文档最初设想在 DSH 源码上增加插件。DSH 0.2.x 支持 bundle：一个 npm 包在 `package.json` 的 `dsh.bundle.patch` 中声明 cordis patch，`dsh plugin --profile <名> add <包>` 装入配置档，叠加顺序为 base → web-app → bundle → profile → `--patch`。

## 决定

- 我们的全部 DSH 侧代码放在 `dsh-bundle/`：两个原生插件（rescue-approval、rescue-audit）+ 5 个 Agent 预设 + 关闭不需要的内置预设。
- 不修改、不 fork DSH；锁定 0.2.1-rc.4。
- 自定义大模型等可选项放在 `profiles/*.patch.yml`，用 `--patch` 叠加。

## 后果

- 升级 DSH 只需重跑 `docs/validation-checklist.md`，无需合并上游代码。
- DSH 插件 API 尚未稳定，所有对 DSH 类型的引用集中在 `src/adapter/dsh.ts`，升级时只改这一处。
- 失去修改 DSH 内部行为的能力；遇到 bundle 做不到的需求，先找扩展点，确实没有再向上游提需求。
