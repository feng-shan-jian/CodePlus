# R19 Leader 独立验收

结论：**ACCEPTED，待精确本地提交**。用户确认充值后，Leader 使用原配置补齐 auto/report 与实际 `codeplus -p` fixed/qa，均正常完成。原官方402、原partial及本次恢复前的阻塞记录保留，没有覆盖失败证据。基线 `cef8821a78447e5dde418cb1d68c6ac73c81fdc7`，唯一集成目录 `D:/CodePlus`，分支 `codex/rag`。

## 独立验证

相关回归 **318 passed / 0 failed / 0 skipped**，JUnit 661.02秒；普通宿主 **139 passed / 0 failed / 1 skipped**，9.79秒。唯一跳过为当前 Windows 无适合 symlink 逃逸样例文件。两组从最终私有安装包加载，并显式传入已核SHA的回答计量器；生产代码在补测前后保持。

Leader 重建最终 host/RAG wheel及sdist，wheel哈希与执行者一致；host143个及RAG72个生产资源在源码、wheel和core/cuda安装逐字节一致。两套清洁环境分别安装直接wheel与sdist重建wheel，pip check、固定计量器、SQLite建库及七个已改模块字节核对通过，CPU宿主未导入torch。十一份旧SQL未变，没有schema迁移。

私有19549/9110实际GPU/Milvus验证45.09秒通过：auto保留完整rerank超长原失败后显式选择BM25/off、Dense/off、Hybrid/on；fixed保留失败后仍按BM25/on重试。所有尝试累计，同run固定版本，失败无新候选。该core动作未调用回答模型，confirmed evidence为零，窗口移除fixture与真实回答证据分开。

## 充值后的实际宿主验收

| 路径 | 结果 | search / open | 输入 | 输出 | 缓存 | 总tokens | 驱动耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| auto/report | completed / finished | 1 / 1 | 6031 | 369 | 3968 | 6400 | 12.91s |
| 实际 -p fixed/qa | completed / finished | 2 / 1 | 7917 | 352 | 4480 | 8269 | 53.01s |

两个run使用同一发布版本，模式来源分别为configured/explicit，预算分别为report/qa；共六次真实模型请求均confirmed、无预留违例，结束pin与host cleanup状态均released。实际CLI进程退出0，stream-json result为completed/finished。已读回两份校验后Markdown：都正确回答blue telescope certificate，并引用原文区间[0,72)及精确英文原文。此小场景证明入口和运行合同，不代替R23语义质量、规模评测或人工校准。

## 代码审查结论

模式来源在配置重建前保留，单次路线与重排选择局部解析，不修改共享服务。fixed 工具 schema 和核心入口都保留冻结权威；auto 省略选择恢复基础配置，失败后的重试仍须显式调用。QA/report 预算与模式正交，复用已有计时、预留/结算、缓存/unknown、收尾与取消完成路径。

两条现有 Agent 循环在真正发送前测量完整序列化请求并裁剪当前可见正文，独立 raw-body 门保留。裁剪后的 source spans/returned_spans 同步，spill 后再次裁剪不从归档补回未见内容；分页 cursor 从实际尾部继续并保留 previous_cursor 的业务上界。只有有效且预算受理的分页才移走对应旧 tool pair，其他 pair 保留；已受理读取失败恢复旧页供收尾。历史 confirmed 引用资格保持，新尾部仍须本轮真实发送确认。

实际 `-p` 路径使用 Agent，知识库 ask 权限无交互时 DENY；普通任务原行为保持。这由调用真实函数的正式测试验证，但该受控测试不证明官方提供方正常回答已经通过。

## 交付与保留边界

再核对18路径最终补丁、82份执行者原始证据、6537个继承路径、旧SQL及根双语README。Leader的checklist状态更新，以及分派卡末尾多余空行的格式修正，均有明确记录。执行者的原402记录和当时未通过结论是历史交接记录；本次接受结论以本报告和[独立验证](R19-leader-validation.json)为准。

Leader最初汇总脚本将数据source_sha256误比为驱动哈希而退出1，修正汇总目标后通过，原脚本和失败记录保留；未修改生产代码或为此重跑无关测试。私有资源按归属交用户另派清理，清理不构成提交门槛。没有push、发布或Linux/主包迁入完成声明。R20报告保存/继续研究、R21管理命令、R23数值冻结及R24总体验收继续后续实现。
