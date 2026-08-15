# JLC Global AI Factory Command Center

独立 React UI Shell。第一阶段只实现首页 Cockpit，并只读取现有 `http://127.0.0.1:8765/api/workbench/*` 接口。

不替换旧 `/workbench`，不修改后端 API、数据库、任务执行、AI pipeline、上传和队列逻辑。

## Run

```bash
npm install
npm run dev
```

可选 API 地址：

```bash
VITE_WORKBENCH_API_BASE=http://127.0.0.1:8765 npm run dev
```
