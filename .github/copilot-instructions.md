## Copilot Agent 运行环境说明
 - **预装工具**：
    - `jq` (JSON 处理)
    - `xvfb` (虚拟显示)
    - `tree` (查看目录结构)
    - `ripgrep` (高性能文本搜索)
    - **Playwright (Chromium)**：优先用于验证渲染、执行 E2E 测试或查看动态内容。
 - **代码校验与格式化**：
    - 环境已通过 `tsc` 类型检查和 `eslint` 代码检查。
    - **Prettier**：在提交或运行测试前，可以使用 `npx prettier --write .` 修复格式。
 - **构建与测试验证**：
    - 在完成功能开发后，运行 `npx tsc -b` 和 `npm run lint`。
    - **Vitest**：单元测试请使用 `npx vitest` 或 `npm test`。
    - **Knip**：运行 `npx knip` 自动发现未使用的文件、导出和依赖。
 - **模拟环境 (Mocking)**：
    - **MSW (Mock Service Worker)**：用于拦截网络请求。无论使用原生 JS 还是 React，都应在 `src/mocks/handlers.js` (或 .ts) 中定义模拟接口，避免硬编码。
    - **使用方式**：在入口文件中加入以下代码即可启动模拟：
      ```javascript
      if (process.env.NODE_ENV === 'development') {
        const { worker } = await import('./mocks/browser')
        worker.start()
      }
      ```

## 开发规范与建议
 - **目录结构探索**：
    - 在开始任务前，运行 `tree src -L 2` 了解模块分布。
    - 使用 `rg "TODO:|FIXME:"` 快速定位待处理事项。
 - **解耦开发**：
    - 新功能开发优先通过单元测试 (Vitest) 或 Mock 接口 (MSW) 驱动，确保逻辑在无后端环境下可验证。
 - **代码审查准备**：
    - 提交前务必运行 `npx prettier --write .` 修复格式，并运行 `npx knip` 确保无遗留废弃代码。

## 示例测试模型
 - **Live2D (Model3)**: `https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display/test/assets/haru/haru_greeter_t03.model3.json`
