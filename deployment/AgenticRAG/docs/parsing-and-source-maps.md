# 解析与分块

Markdown、TXT 按 UTF-8 解码，移除开头 BOM，将 CRLF/CR 转为 LF，其余正文保留。Markdown 识别标题、段落、列表、表格和代码块；TXT 按空行分段。

分块依次选择章节、结构块、句子和 tokenizer 边界。当前配置为完整 Embedding 输入最多 **512 tokens**、重叠正文最多 **64 tokens**；标题和特殊 token 计入输入长度。超长结构继续切分，空白和仅标题内容只保留导航。

每个片段保存所属文档版本、章节和原文区间。区间为规范文本的 Unicode codepoint `[start,end)`，SourceMap 将其对应到原件字节，因此重复段落也有独立位置。检索标题单独用于索引，不混入引用正文。

解析结果随版本归档，重复读取直接复用。修改解析或分块配置需[重建索引](model-switching.md)。
