# 求职材料 API

所有接口位于 `/api/v1`，身份只来自 JWT。`ResumeVersion` 和 `JobTarget` 保存后不可原地修改；新内容创建新版本，历史面试继续引用原冻结版本。

## 简历文件抽取

```http
POST /resume-versions/extract
Content-Type: multipart/form-data

file=<PDF | DOCX | TXT>
```

约束：

- 文件最大 5 MiB；
- 只支持 PDF、DOCX、TXT；
- 服务端仅在内存中抽取文本，不保存原文件；
- 不执行 OCR，加密 PDF、扫描版/无文本 PDF、损坏文件会被拒绝；
- 返回的 `source_text` 是可编辑预览，不会自动创建 `ResumeVersion`。

响应：

```json
{
  "filename": "resume.pdf",
  "media_type": "application/pdf",
  "character_count": 1234,
  "source_text": "..."
}
```

## 保存简历版本

```http
POST /resume-versions
Idempotency-Key: <key>
Content-Type: application/json
```

粘贴文本默认使用 `source_type=pasted_text`。文件抽取并经用户确认后使用：

```json
{
  "label": "后端工程师简历",
  "source_type": "uploaded_file",
  "source_filename": "resume.pdf",
  "source_media_type": "application/pdf",
  "source_text": "用户确认后的文本"
}
```

文件名和媒体类型作为安全元信息进入 `structured.source_file`，原文件不进入数据库。

## 其他端点

```text
GET    /resume-versions
DELETE /resume-versions/{resume_id}
POST   /job-targets
GET    /job-targets
DELETE /job-targets/{target_id}
```

## Agent 材料优化

```text
POST /resume-assessments/optimize
GET  /resume-assessments
GET  /resume-assessments/{assessment_id}
PUT  /resume-assessments/{assessment_id}/claims/{claim_id}/decision
POST /resume-assessments/{assessment_id}/rewrites/apply-batch
```

优化输入冻结 ResumeVersion、JobTarget，以及可选的已完成 InterviewSession。没有面试证据时
运行 `pre_interview` 诊断，所有依赖个人经历的结论必须保持 `insufficient_evidence`；有面试
证据时运行 `evidence_enhanced`。Run 完成后客户端必须通过 `result.assessment_id` 精确加载结果，
不能假设列表第一项属于当前 Run。

Claim 必须保存 `source_start/source_end/source_hash`。批量应用仅接受同一 Assessment、同一父
版本的 accepted decisions，并校验 source span/hash；一次请求只创建一个子版本。单条 Apply
接口进入 deprecated，不再由普通前端调用。
