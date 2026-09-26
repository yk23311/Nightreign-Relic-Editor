# 字段映射表（一期）

> 数据来源：`Elden Ring Nighreign - Hexinton 1.1.0 汉化版.CT`  
> 外置配置：`data/mapping.json`（CT 更新时用 `mapping/ct_parser.py` 重导出）

## 已装备 6 槽

| 槽 | kind | 索引表达式 | 属性1 | 属性2 | 属性3 | 减益1-3 |
|----|------|------------|-------|-------|-------|---------|
| 1 | STD | `8+8*[Gaitem]` | `+18` | `+1c` | `+20` | `40/44/48` 只读 |
| 2 | STD | `8+8*[Gaitem+4]` | 同上 | | | |
| 3 | STD | `8+8*[Gaitem+8]` | 同上 | | | |
| 4 | DoN | `8+8*[Gaitem+c]` | 同上 | | | |
| 5 | DoN | `8+8*[Gaitem+10]` | 同上 | | | |
| 6 | DoN | `8+8*[Gaitem+14]` | 同上 | | | |

- 基址：`csgaitem`（指针）
- 类型：u32 效果 ID
- 偏移为 **CE 十六进制**

## 枚举

| 文件 | CT 列表 | 用途 |
|------|---------|------|
| `data/effects_std.json` | RelicIDStd | 标准槽属性 |
| `data/effects_don.json` | RelicIDDoN | 黑夜深处槽属性 |
| `data/effects_debuff.json` | RelicIDDebuff | 减益（校验） |
| `data/effects_all.json` | RelicID | 反查 |

格式：`Effect_ID : Roll_Order | Compatibility | Req_Debuff | Roll_Group | In_Game_Name`

## 符号

| 符号 | 说明 | 状态 |
|------|------|------|
| `csgaitem` | CSGaitem 指针 | rva 待接真机校准 |
| `Gaitem` | 6×u32 槽 ID 表 | 同上 |

## 一期不写入

减益 1–3、遗物类型、注入背包、参数修改器、武器/道具。
