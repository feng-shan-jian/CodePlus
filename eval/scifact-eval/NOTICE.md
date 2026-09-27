# 来源与署名

- 原始 SciFact：David Wadden 等，*Fact or Fiction: Verifying Scientific Claims*（EMNLP 2020）；[论文](https://aclanthology.org/2020.emnlp-main.609/)、[官方项目](https://github.com/allenai/scifact)。
- BEIR 格式：Nandan Thakur 等，*BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*（NeurIPS 2021）；[官方项目](https://github.com/beir-cellar/beir)、[SciFact 数据卡](https://huggingface.co/datasets/BeIR/scifact)。
- 数据来源：[BEIR 官方发布的 scifact.zip](https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip)，MD5 与官方表一致；另记录归档和四个原始文件的 SHA256，见 `source-lock.json`。BEIR 仓库修订仅用于固定来源说明，不表示 ZIP 由该提交重新生成。
- 数据卡标注许可为 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。本地适配保持原 ID、标题、正文、查询、相关性标签和官方拆分；只将标题与正文包装为 Markdown 导入文件。
- 额外的本地 `development` 选择从官方 train 排除与 test 文本重复的查询 `871`、`1291`，保留原始 train/test 文件。此选择不是上游发布的官方 dev split。
- 指标工具：[ir-measures](https://ir-measur.es/en/latest/getting-started.html)，调用 pytrec_eval provider。此处接入检索指标，不声称复现 BEIR 论文分数。
- 补充句子证据：[SciFact官方数据归档](https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz)，来源于[官方下载脚本](https://github.com/allenai/scifact/blob/master/script/download-data.sh)。2026-09-27归档SHA256为`11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be`；只读取corpus与train声明，用于离线证据完整性评分，不导入检索语料。文件级哈希、全部查询／语料／块偏移对齐记录位于`runs/annotations/scifact-20260927/alignment.json`。

本目录与 MultiHop-RAG 独立。不得将本组查询、qrels、评分结果或 MultiHop 文件混入本组导入语料。
