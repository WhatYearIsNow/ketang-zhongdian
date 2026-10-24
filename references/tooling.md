# 本地脚本约定

维护者另可运行 `python scripts/build_release.py --output ../ketang-zhongdian-0.2.1.zip`。它读取项目根 `release-manifest.json`，仅打包明确列出的文件并附 `MANIFEST.sha256`；固定文件顺序、权限与 ZIP 时间。拒绝路径越界、符号链接、缺失文件与大小写冲突。输出必须在源码目录以外，保护源文件；不同既存包需显式 `--overwrite`。退出 0 为写入/同内容，2 为坏输入/写入失败；stdout 为 JSON。相同 Python/zlib 环境字节可重复，跨 zlib 版本不保证压缩字节相同。它不自动跑测试，也不自动批准清单里的素材公开。

Python 3.10+，只用标准库，均支持 `--help`。命令中的相对路径以当前工作目录解析；可在任意目录用脚本绝对路径执行。无需 pip 安装、API 密钥或联网。

## transcript.py

```text
python scripts/transcript.py INPUT.docx --output OUTPUT.txt
python scripts/transcript.py INPUT.txt --encoding gb18030 --output OUTPUT.txt
```

输入 TXT/DOCX；输出必须是另一个 `.txt`。UTF-8/BOM 与 BOM UTF-16 自动识别，其余编码显式指定。只规范换行和行首 Speaker 编号，不纠 ASR、不改变音频时间码。DOCX 依主文档顺序读段落及表格单元格，文本框/修订/图/脚注/OMML公式等警告提示补读；不是渲染器或完整 Word 提取器。

先准备全部输出，再在目标同目录写临时文件并发布；同内容跳过，不同内容默认拒绝。确需替换时用 `--overwrite`。不接受输入=输出或输出符号链接。写入错误保留已有目标；临时文件尽力清理。Windows 用不替换既存文件的 rename，其他平台用同目录 hard link 进行无覆盖发布；文件系统不支持时安全失败，不静默回退成截断写入。并发多任务应各用独立输出路径。

stdout 为 JSON：`ok/status/characters/sha256/warnings`，错误有 `error`。ASCII 转义 JSON 可在旧 Windows 控制台解码后完整还原中文。退出码：0 成功/同内容；2 输入、编码、写入错误。空稿拒绝。TXT 与 DOCX XML 部件限 32 MiB；这不是通用不可信文档沙箱，复杂文档用专门工具并核实提取覆盖。

## calibrate.py

```text
python scripts/calibrate.py tests/fixtures/anchors.json --tolerance 120
```

输入对象的 `anchors` 非空数组。每项有完整 ISO `photo_time`、非负有限秒数或 `mm:ss`/`h:mm:ss` 的 `transcript_time`、非空 `evidence`。完整例子见合成 fixture。可跨午夜；全无时区或全相同 UTC offset，不能混用或跨设备时区猜测。拒绝重复照片时刻与非法时分秒。

输出 JSON：`status`、`candidate_recording_start`、`offset_reference_time`、`median_offset_seconds`、各点残差、`max_residual_seconds`、`limitations`。偏移是相对参考日零点的秒数。单点 `provisional`；多点符合阈值 `candidate`；超阈值 `inconsistent`。退出码：0 可作候选；1 不一致；2 坏输入。candidate 不等于实际校准成功。

## check_links.py

```text
python scripts/check_links.py NOTE.md --root VAULT --json
```

默认 root 为笔记父目录。标准 Markdown 相对链接以笔记目录为基准，可合法 `../` 但不得越 root；带路径 wikilink 按 vault root，裸文件名须全 root 唯一，省略扩展名只推断 `.md`。会解析 URL 百分号与 HTML entities，检查真实文件，遇重名/越界/缺失报告问题。不跟随外部 URL，不检验页内标题、区块或音频 `#t=` 的实际有效性。

支持单行 Markdown 行内链接/图片、完整/折叠/已定义快捷引用及单行定义；括号平衡、转义、尖括号目的地、可选标题；支持 `[[path#anchor|alias]]` 与嵌入。兼容 Obsidian 的裸空格目的地。屏蔽 fenced/inline code 和 HTML 注释。

有限解析器不是完整 CommonMark：跨行标签/目的地/定义、嵌套 Markdown 链接、本地 HTML href/src 会报 unsupported；引用中的嵌套图片可检查。缩进代码不会自动屏蔽，请使用 fenced code。未定义 `[普通文本]` 依 CommonMark 当文本，显式 `[文本][缺失]` 报错。脚注、模板语言不作语义解释。对未实现的新语法应补测或人工核验，不能用退出 0 证明完整 Markdown 渲染正确。

JSON：`ok/checked/skipped/issues`，issues 含 `line/target/kind/message`；另有锚点未验证提示。退出码 0 已支持本地链接均解析，1 发现链接问题或不支持语法，2 输入不可用。检查器不写笔记、改链接或判定图片/来源真实。
