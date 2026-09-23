# R21 分派：现有命令与配置使用流程

基线 00fbbbb82509c61e43b3b01be9c68273498ab716，唯一目录 D:/CodePlus，分支 codex/rag。R20实际验收提交后才派发；全新执行者唯一写入，Leader执行期只读，STOP_WRITE后独立验收并精确本地提交。当前Windows R00–R24，清理交用户另派，不push/发布/新worktree，不造另一套Agent。

先读取任务卡/checklist R21、plan D16/D29/D49/D52/D53、architecture T07/T09、host-integration-contract第7节、R20最终API/入口/测试。原plan描述的旧handler/--knowledge是历史事实，不能恢复旧业务与旧ID；当前实际--knowledge-library、mode及R20参数保持兼容。

1. 在现有 /knowledge 与 codeplus -p 形式接通create/use/import/status/sources/reimport/remove/retry/off/open，以及批次恢复/放弃、模型更换confirm/retry/keep_original。研究continue沿R20接口，不能与批次恢复语义混淆。无需独立产品CLI/WebUI或评测前端。帮助、参数、返回状态与真实实现一致。
2. 复用Catalog.create_library/get_library与必要最小查询接口；ingestion.begin_changes/build_changes/retry_failed/mutation_summary；inspect_recovery/continue_recovery/abandon_recovery；model_switch.inspect_model_switch/apply_model_switch；citations.open_citation。不得在handler复制事务、CAS、发布、重试或恢复业务。状态准确显示当前发布版、待恢复任务、已完成/失败文件与实际模型身份/差异。
3. 普通管理命令按用户明确操作执行，不增加额外通用确认。D16模型重建必须有明确confirm，失败后的retry不等于初次授权，confirm不能偷偷重试，keep_original不破坏原版。恢复/放弃对选定批次使用实际OwnerToken；缺少选择、无终端、空答复、取消、超时保持待确认，不当作同意。已变更输入的retry沿原accept_input_changes规则。
4. TUI、实际-p与已有RemoteServer装配同一功能。Remote当前knowledge_feature_available=False，R04契约明确R21正式启用；复用现有WebSocket命令/权限事件，不建新界面。缺可选包/配置给可操作错误，不能偷偷退回普通聊天答知识问题。
5. Knowledge任务显式选择库/模式，单次覆盖不改默认配置；普通聊天、命令管理和宿主权限--mode不受强制检索影响。R20报告路径、显式继续及真实产物/运行状态贯通各入口；会话选择/运行ID只保存必要元数据，不把历史正文或旧evidence注入新run。
6. 真实-p的最终退出状态由真实RunOutcome决定；不能仅打印failed JSON却进程退出0。异常、无证据、预算部分完成、取消、待确认需可定位，沿宿主现有退出约定确定并文档化，不随意改普通任务退出行为。启动配置错误也有准确输出，密钥不进入错误/日志。
7. 同步导入/重建/恢复由适配层在后台执行，保持现有真实 mutation owner 与 cancelled 回调；TUI/Remote 事件循环仍可接收取消，等待协程取消不代表线程已经结束，结束前不得显示库已空闲或提前关闭 provider/backend。复用现有运行/命令状态，不建通用任务引擎。
8. Remote知识流使用aclosing及实际任务取消/断连路径：当前_ws_handler finally仅移除连接，_handle_user_message只在新event时看cancel且finally清空permission futures。新知识装配必须在HTTP/权限等待时也能退出，拒绝未答权限并走scope finally，保留实际reader/pin完成约束。只改必要功能接点，共用部分变更带普通Remote回归。

验证：实际解析器/handler与真实宿主函数，不能只有fake adapter返回；创建两库，导入/更新/删除/失败重试/恢复或放弃/历史引用/模型选择链路，读回当前版本和原历史。空间/中文路径、错误UUID/缺输入、跨库、重复或失效proposal、并发待恢复与取消按实际风险覆盖，不新增理论防御体系。实际-p stdout/JSON/退出码、TUI必要工作流、真实WebSocket取消/断连及权限等待有证据；故障注入与真实网络分别标明。
最终host+RAG包同时重建安装，从私有site-packages执行；显式R12_ANSWER_TOKENIZER与固定SHA。小型真实GPU/Milvus/已批准DeepSeek完成日常用户命令→问答/报告→可回看结果；不重复200题，不把接口成功当R23答案质量。
保持旧11份SQL与用户数据，优先现有schema/JSON。正式文档中说明可选安装/配置/最短日常使用，不泄漏开发实现细节到产品提示。允许宿主__main__/app/remote/config/命令/必要会话元数据的最小接点，以及adapter/catalog查询/正式测试/模块文档；根README、冻结评测数据、清理范围保护。
资源和交付：私有根/端口由Leader派发时核空闲；普通复制已结束R20Leader环境，缓存只读，原失败与必要复验留索引。精确R21-files、执行记录、commands/cwd/exits/hash/资源归属；不stage/commit，全部结束STOP_WRITE。

## 本次派发的实际基线与资源

R20 已本地提交 00fbbbb82509c61e43b3b01be9c68273498ab716，31 个精确路径及父提交/全部 blob 已复核，index 空。R20 Leader 后记与 checklist 的真实 SHA 更新尚未另作提交，按既有规则随下一次正常阶段提交；执行者须冻结并保留。

最终 R20 当前包：host f71acfb81fa33ddb9b5dab9b4316a8a427316afe4457162345a18c8c7b64e91b，RAG 48aba508386ad290fc46e83a558012b4862e20db2b2a577b27aaefb1986258ac。125 项直接相关回归、清洁安装通过；同字节宿主已有148 passed/1 Windows条件skip。Leader 实际新链前三轮 completed，删除轮 partial/search_limit，严格正常整链 FAIL 原样保留；其 D14/D55 功能边界通过，执行者正常四轮也经 Leader 独立数据库/文件回读通过。搜索收敛及无支持的标注猜测留 R23，不能在 R21 宣称质量已过，也不为此重复调提示或扩大预算。

唯一可写工作区 D:/CodePlus。本执行者私有根 C:/Users/18221/AppData/Local/Temp/codeplus-r21-executor-20260923；预留19552/9113。Leader未来验收预留19553/9114，当前不启动。派发前这四端口无监听、两个R21根不存在，执行者启动前仍须实查。普通复制已结束的 C:/Users/18221/AppData/Local/Temp/codeplus-r20-leader-20260923/core、cuda 作为本项环境，不硬链接、不回写 R20 环境；固定 tokenizer 可复制到自己的根并核SHA。共享 C:/Users/18221/.cache/codeplus-agenticrag/models 只读。

保留 6537 继承路径、根双语 README、11 份旧 SQL、R20 提交全部证据、R20 Leader后记/真实SHA台账和本分派卡。R20 Catalog 回读关闭连接曾自动移除共享内存与空WAL，主库及290份其余原件未改；已明确记录，别把运行中SQLite sidecar当长期冻结原件，也不要恢复或清理前项资料。

实现前按当前代码确认复用点和实际命令语法，常规选择自主推进。不要增加泛化任务框架、重复业务API、每层理论校验或无关重构。使用原已批准回答提供方配置，充值后可做必要真实验证；不得把凭据写入参数日志或证据。先完成实际功能与适用验证，再给出精确 R21-files、执行记录、原始证据/命令/cwd/退出码和自有资源清单；所有命令结束后明确 STOP_WRITE。Leader从派发后只读，不同时写文件；不stage/commit/push，不额外分派子agent。
