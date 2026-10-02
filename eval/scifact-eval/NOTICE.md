# 来源与署名

- SciFact：David Wadden 等，*Fact or Fiction: Verifying Scientific Claims*，EMNLP 2020。[论文](https://aclanthology.org/2020.emnlp-main.609/)、[项目](https://github.com/allenai/scifact)。
- BEIR：Nandan Thakur 等，*A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*，NeurIPS 2021。[项目](https://github.com/beir-cellar/beir)、[SciFact 数据卡](https://huggingface.co/datasets/BeIR/scifact)。
- 数据来自 [BEIR 官方 scifact.zip](https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip)，数据卡标注许可为 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。固定来源、版本和文件 hash 见 [source-lock.json](source-lock.json)。
- 补充句子证据来自 [SciFact 官方归档](https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz)，归档与位置映射见 [alignment.json](runs/annotations/scifact-20260927/alignment.json)。

本地保留原 ID、标题、正文、查询和相关性标签，另生成 Markdown 导入文件及 development 子集。指标由 [ir-measures](https://ir-measur.es/en/latest/getting-started.html) 的 pytrec_eval provider 计算。
