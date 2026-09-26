# 架构说明

代码落地对应：

| 层 | 目录 | 职责 |
|----|------|------|
| UI | `app/ui/` | 主窗口、深色主题、3 步流 |
| 服务 | `app/services/` | RelicService / PresetService |
| 领域 | `domain/` | 模型、枚举、规则 |
| 映射 | `mapping/` | ct_parser、address_expr、mapping_config |
| 基础 | `infra/memory/` | IMemoryBackend、指针、事务、符号 |

## 写入路径

```text
UI 选词条 → FieldWrite[] → rules.validate → MemoryTransaction
  → 备份 u32 → write_bytes → 失败回滚 → WriteReport
```

恢复备份走独立路径：地址按 (slot, field) 重新解析，且 `skip_rules=True`。
原因见 `RelicService.restore_backup_file` 的 docstring。

## 地址求值（改这里之前先读 `infra/memory/pointer.py` 的注释）

CE 的 `getPointerAddress` 语义是「**每层先解引用、再加偏移**」，且 XML `Offsets`
的**第一项是最终字段偏移**（最后应用）：

```text
p    = *[csgaitem] + (8 + 8*[Gaitem + (slot-1)*4])
addr = *[p] + field_offset

field_offset = +18 / +1c / +20     （属性 1 / 2 / 3）
               +40 / +44 / +48     （减益 1 / 2 / 3）
```

- `[Gaitem+N]` 按 **32 位** 解引用（与 CT 写入的 smallint 缓存兼容）。
- `*[csgaitem]` 与 `*[p]` 按 **64 位** 解引用。
- 数字常量按 **CE 十六进制** 解释（`1c` = 0x1C，`c` = 0xC）。

> **历史教训**：本节曾写成
> `addr = *[csgaitem] + (8+8*[Gaitem+(slot-1)*4]) + field_offset`，
> **少了一层解引用**，且 `field_offset` 只列了属性、漏掉减益。
> 照那份公式实现会全部读错。
