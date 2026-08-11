# 案例包（Case Pack）

每个子目录 = 一个可一键导入的演示/回归案例。

## 新增案例（零业务代码）

1. 创建目录：`demo_output/cases/<case_id>/`
2. 写入 `manifest.json`（见 `audit_yikang_3y/manifest.json`）
3. 放入 PDF/Excel，或把文件放到搜索目录（如项目旁 `测试文件/`，文件名与 manifest 一致）
4. 可选：`gold.json` 供回归对账

然后调用：

- `GET /api/import/cases` — 列出
- `POST /api/import/case/<id>` — 载入（支持 `aliases`）

任意企业日常使用请走 **`POST /api/import` 多文件上传**，不必建案例包。
