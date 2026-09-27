# 沙盒 cloudstorage 写回（POC-3）

> 对应 PRD §12 Phase 0 POC-3（JSON 写入 + 回滚，M3 写回路线核心验证）。
> 沙盒副本操作，不接触真实 Steam 文件（CLAUDE.md 铁律）。

## 目录结构

```
cloudstorage/
├── data/                            # ⚠️ 工作副本（gitignore，不入库）
│   └── cloud-storage-namespace-1.json  # 复制自 leveldb/real_cloudstorage/（真实数据）
├── scripts/
│   ├── writeback.py                 # 核心库：原子写入 + 备份 + 回滚 + upsert/软删除
│   └── test_writeback.py            # pytest 断言（8 项）
└── README.md
```

## 关键实测结论（2026-08-11）

1. **Steam 序列化格式字节级匹配**：`json.dumps(ensure_ascii=False, separators=(',', ':'))`——
   无空格紧凑、无换行、中文 UTF-8 直写。原子写入保持此格式才能与 Steam 客户端完全兼容
2. **条目结构**：`[key, meta]`，meta 六字段 `key/timestamp/value/version/conflictResolutionMethod/strMethodId`；
   value 为 JSON 字符串 `{"id","name","added","removed"}`；软删除 `is_deleted=true` 且无 value
3. **version 语义存疑**（Phase 1 vdf_ops 校准）：已存在条目按 +1 递增；新条目起点 "1"
   为沙盒约定，Steam 客户端新条目 version 起点未实测

## 机制（对应 PRD M3）

| 机制 | 实现 |
|---|---|
| 原子写入 | 同目录 .tmp + os.replace（参考 dtq1997）；失败原文件不变 |
| 备份 | 时间戳目录 `<backup>/<ts>/cloud-storage-namespace-1.json`（模拟 M3.1 `%APPDATA%/KuJi/.backup/<ts>/`） |
| 回滚 | 备份文件覆盖（M3.2 写入失败自动回滚）；回滚后 sha256 与备份一致 |
| 软删除 | meta 替换为 `{key,timestamp,is_deleted,version}`（与真实数据一致） |

运行：`pytest tests/sandbox/cloudstorage/scripts/test_writeback.py -v`
