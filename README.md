# 课堂重点 / Classroom Highlights

将课堂逐字稿、可选的课堂照片与课程资料整理为可溯源的 Markdown 复习笔记。

An agent skill for source-grounded classroom notes from transcripts, optional photos, and course materials. Instructions and notes default to Chinese; the local Python helpers have no third-party dependencies.

## 能做什么

- 合并逐字稿、实际看过的照片和教材/课件证据，保留课堂范围。
- 区分课堂讲授、资料补充、播放材料和未核验内容。
- 用内容匹配校准照片时间；不能校准时明确采用相对时间。
- 增量复核旧笔记，保护已有手工音频链接。

需要可读逐字稿；只有音频时先完成转写。照片与教材可选；有照片时需要多模态能力。此项目不包含 ASR、OCR 服务、课程资料或模型 API。

## 安装与使用

将本目录整体放入你的 agent 支持的 skills 目录，保持目录名 `ketang-zhongdian`，不要只复制 `SKILL.md`。也可直接让 agent 读取本目录的 [SKILL.md](SKILL.md)。具体 skill 发现与调用方式由宿主提供。

示例请求：

> 使用 $ketang-zhongdian 整理这次课的逐字稿和照片，资料在我指定的课程目录中。无教材处标明未核验，保留原音频链接。

主入口只载入基本规则；详细程序从 [SKILL.md](SKILL.md) 路由到 `references/`，模板在 [assets/note-template.md](assets/note-template.md)。兼容课程包默认路径，也支持独立目录。

## 本地工具

Python 3.10+；无第三方运行依赖。在本目录执行（空格路径需加引号）：

```text
python scripts/transcript.py INPUT.docx --output OUTPUT.txt
python scripts/calibrate.py tests/fixtures/anchors.json
python scripts/check_links.py NOTE.md --root COURSE_OR_VAULT_ROOT --json
python -m unittest discover -s tests -v
```

维护发布时执行 `python scripts/build_release.py --output ../ketang-zhongdian-0.2.1.zip`。打包仅使用 [发布清单](release-manifest.json) 中的文件，并生成逐文件 SHA-256；新增文件须审查后显式加入清单。文件时间戳固定，同一环境和相同输入产生相同 ZIP。已有不同内容的包不会默认覆盖。发布前先运行测试，打包成功本身不代表测试通过。

工具不会自动生成最终笔记、理解照片或判断老师是否讲过。逐字稿工具默认不覆盖不同内容；链接检查只读、不访问网络。语法范围、输出与退出码见 [脚本说明](references/tooling.md)。

## 开发与发布

- [重构方案与验收边界](docs/REFACTOR_PLAN.md)
- [测试说明](tests/README.md) 与 [行为评估用例](evals/cases.json)
- [贡献约定](CONTRIBUTING.md)
- [验证报告](docs/VALIDATION.md)

只发布本项目目录。所有样例均为人工合成，课堂录音、照片、扫描教材和私人路径不属于发布内容。GitHub Actions 已配置 Windows、Linux、macOS 与多个 Python 版本；实际远端运行结果以发布后的 CI 为准。

## License

[MIT](LICENSE)，仅适用于本项目代码与文档，不授予任何外部课堂资料的再分发权。
