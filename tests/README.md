# 测试说明

在项目目录执行：

```text
python -m unittest discover -s tests -v
```

全部运行测试只用 Python 标准库和临时目录；fixtures 均为人工合成。普通机器无需环境变量。如受限环境不允许系统临时目录，先创建工作区临时目录并将 `TEMP`/`TMP` 和 `KETANG_TEST_TMPDIR` 指向它；不要因此改系统全局配置。

| 层级 | 覆盖 | 不证明什么 |
| --- | --- | --- |
| transcript 单元/CLI | BOM/显式编码、DOCX 段落表格、遗漏警告、幂等/覆盖保护、写失败/并发 | ASR 准确、复杂 DOCX 全文完整 |
| calibrate 单元/CLI | 长时长、跨午夜、时区、单点/一致/冲突、数值校验、退出码 | 真实照片内容匹配或实际误差 |
| links 单元/CLI | Markdown/Obsidian、转义解码、歧义、越界、坏输入、只读 | 完整 CommonMark、页内锚点、图片语义 |
| release 单元/CLI | 显式清单、校验和、字节重复性、未列文件排除、越界/覆盖保护 | 清单内文件已获素材授权或课堂事实正确 |
| 独立行为测试 | 无教材、来源冲突、链接保留等，[cases.json](../evals/cases.json) | 所有模型/课型表现、真实视觉准确率 |

行为评估让独立 agent 只看到请求、skill 与必要输入，在隔离目录实际产出。检查输出与原始字节、链接和证据，不提供期望答案让它照抄。各用例执行情况在 [验证报告](../docs/VALIDATION.md) 如实记载；未执行用例不能按“已通过”计数。

Windows 缺创建符号链接权限时对应测试可跳过并记录；CI 在 Linux/macOS 再覆盖该项。仓库 CI 矩阵是未来远端验证，不能冒称本机跑过各平台。宿主提供的 skill frontmatter 校验可附加运行，不作为本仓库运行依赖。
