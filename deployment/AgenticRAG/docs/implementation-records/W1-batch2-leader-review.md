# W1 第二批 Leader 接入验收

状态：ACCEPTED_MAIN_RESOURCES_CLEANED_HELPER_BLOCKED。唯一集成目录 D:/CodePlus、分支 codex/rag，基线为第一批本地提交 8c764644c57e59c9aa9ae2337f61efc3447d88ee。工程验收和主资源独立清理、Leader复核完成；清理辅助目录被自动审批拒删，单独等待用户指示。代码与完整事实按既有授权精确本地提交，实际SHA以postcommit记录为准；不宣称全部临时资源清理完成，R15未获得写权限。

## 冻结与实际审查

原 v1 patch 为 111654 bytes，SHA256 c6157e107cf72a552221c0863b8e020687174f1ff062a17efd87b18df84790f8；manifest SHA256 049a486e11ed94ecfe730118168b3aa3d9c8178cec577f81c623822054f8bc65。25路径仅含 BS02、BS03、BS04 单次搜索复用、BS07、BS08 的生产实现、正式测试和契约文档。W2 worker/诊断/request IO identity/Milvus finalize 以及材料迁移均未进入本批。

Leader独立阅读13个生产文件、直接调用链和正式测试。_BuildPreparation绑定catalog对象、store/batch/base/snapshot/request/input manifest、当前owner token、revision和单次生命周期；本候选仍有内部内容seal，最终publication.validate完整重读归档。_InputRead每操作认证完整manifest，每项窄查当前SQL记录，真实归档每次实读，不成为owner授权。SourceSession仅在单次search按库/版本/文档/文档版本复用，每命中检查section/chunk/span/text，新的调用、公开签发/打开和证据交付仍重读。官方Milvus验证器绑定保留精确类型、catalog/storage和正式类算法，不接受实例或子类pass替代；这是责任位置收敛，不意味着消除具体适配器耦合。迁移仅归并2–7逐版本事务，v1身份初始化、v8外键重建、8份SQL和旧迁移指纹均保持。

v1应用前逐项base blob、filter/working-tree-encoding、forward check通过；应用后25个candidate Git blob相等、reverse check通过。跨工作树25份raw SHA均因本地字节形式不同而单独记录，没有重写换行或冒称raw全等。8份SQL在本集成树前后raw完全相同。冻结319个实际源码/测试/资源文件，所有v1检查前后保持。生产13文件+275/-168、净增107行；归档读取次数下降不等于总行数下降或端到端提速。

## 开发入口修订

父审查任务01a0c994-fadc-73f0-9710-e79b485d9494在独立25-blob快照复现P2：正式测试现通过真实适配器构造器注入SDK，但原dev组未声明PyMilvus，core+pytest环境报ModuleNotFoundError。修订仅增加README、pyproject.toml、uv.lock三路径，不跳过原测试。

3路径delta SHA256 ed2e64242da8fce916a486aa12c9e0829b944155b140af26fc0af40651a8235d；完整28路径v2为115251 bytes，SHA256 da86985356438b56b96f35e38209e11a79a0e7c2f8d720cff3d11965aa53df28，manifest SHA256 00f3d6777e0597670b0f59cd1653be9c0f23f048ece364dce038c0fe67b141e0。等待v1所有CPU/真实进程完成后才应用。28个Git blob匹配，原319文件字节未变，冻结扩大为322。全部第三方lock package记录和project runtime/optional extras逐项不变；只增dev的pymilvus==3.0.2及两条锁记录。

Leader从纯W1复制源快照，使用新独有环境，仅执行默认 uv sync --project <snapshot> --locked --python C:/Python314/python.exe，直接安装PyMilvus3.0.2；没有手补SDK。原发布原子性、正式构造器及实例override两个回归：2 passed，无skip。父审查另有fresh默认入口2 passed/7.36s和v1显式SDK后的62 passed，均不替代Leader验收。

## 独立运行证据

- 新Python3.14.3 CPU环境使用R12 require-hashes锁，自有uv cache/copy安装；CUDA为参考环境的独立字节副本。复制前后22380个后代metadata相等，包含根时22381；未安装到共享环境。
- v1安装：host和RAG wheel、sdist构建及CPU/CUDA实际安装、pip check、所有生产py/sql/json字节核对通过。host wheel SHA 9f76716a8c42e4af53f9039563141e53f16438072eb872ea1e94270da6b0ae04；v1 RAG wheel f919cd1cf27646a8683fa45a8f7460c94ef9a9a3b6688ba505e3a08ed417ed94，sdist d98727928374ccd56cbf8101edd4f7cd820dfd5b16e7fe362434542f5d0771be。
- core全量：705 passed /29 skipped，907.99秒，175个已加载模块来自site-packages；host全量：715 passed /3 skipped /1既有warning，34.80秒，152个已加载模块来自site-packages。合计1420 passed /32 skipped。定向251 passed/304.67秒属于core子集，不重复累加。319冻结文件均未变，未把skip计为通过。
- 官方609文件：4041 chunks、0失败、最大完整embedding输入512 tokens，202.264秒。固定生产answer tokenizer重新下载并核对6367257 bytes和SHA c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b。这是处理一致性检查，不是语义检索质量评估。
- v2发行：wheel SHA bfe0073b607ba30e5cf1b3e0d684f4cadad73ec628a30ebbd4fd91ab1a355ddb；sdist d705a973b5f22ca9ab2549f940b8676d3c418b8437048348aa46e86e2a1d6f7b。各自全新core-only环境安装及pip check通过，没有PyMilvus或torch。与v1的63份生产源码/资源及全部Requires-Dist完全相同，核心仍4项，PyMilvus仅milvus extra。CPU/CUDA集成环境也重装并核对最终wheel。
- 真实服务：独有project codeplus-w1-batch2-leader-20260923，端口19542/9103，3容器/3卷/1网络；本地镜像ID与R14一致，--pull never。实际4070 Laptop GPU执行普通变更烟测PASS及13场景恢复矩阵PASS，12次实际PID+birth终止可核；两次恢复产生3个不同epoch/revision/collection，后两次无重复编码。actual late insert旧集合回执和新集合不受影响、无持久ownership proof集合保留等由正式驱动和独立读回核对。v1生产源码与v2逐字节相同，保留各次真实发行身份，不将原报告改称v2重跑。
- 使用已提交R13 a4fede1c3f0a09202710e53debdbac3307c0911d真实构建v7包，安装到独有host/CUDA环境后产生GPU/Milvus历史数据，再升级最终v2 wheel：PASS。历史行/trigger/FK、归档、类型化run、引用保持；迁移故障回滚和未完成批次恢复通过。旧worker正常idle退出后才切换安装，未操作共享锁。
- Leader独立读回15份真实数据库：schema8、integrity、FK、发布/artifact行和全部登记归档实际字节/SHA通过。对late-insert新集合直接SDK读取一条完整1024维dense记录，独立float32 SHA、有限值、L2范数和全部标量等于预期归档。12个被终止的进程出生身份均不再存活。停止本批standalone时，已发布/无变化批次的continue和abandon重放均不打开runtime、不覆盖后续current；原文件已删除时历史citation与升级前完全一致。随后本批standalone恢复healthy。
- smoke、13-case matrix和upgrade之间按实际worker PID+birth确认正常空闲退出，GPU驱动串行。CPU suite未开启R09_REAL/R10_REAL。R14另有6项生命周期矩阵的原阶段证据，本批未重复运行；本批不宣称真实联网Agent回答质量、Linux安装或总体性能收益。

## 原失败与剩余维护项

所有原失败保留：执行方首次缺SDK的两次收集、跨工作树SQL raw换行比较、父审查P2缺SDK复现；Leader的v2 setup末尾也曾错误要求仅模型职责的CUDA环境安装PyMilvus。该次setup-v2.json保持FAIL；default-dev、两个回归、两路clean发行安装均已在此前完成，后续setup-v2-verification.json独立重核环境/322文件/发行物/原XML并按正确职责验证通过，没有改产品代码或绕过断言。一次crosscheck辅助脚本的JS组装SyntaxError发生在任何嵌套工具执行前，没有生成文件；修正后先AST解析再运行通过。只读查找时不存在文件名的错误未运行产品检查。

父审查提出BS02内部全对象seal重复防御过宽：rows/members/spec没有真正跨调用方信任边界，真实外传别名主要是dense。这个维护性项不构成已验候选的生产正确性失败，但BS02和整个W1不得结项。已授权后续独立4路径窄补丁，删除重复全内容摘要、隔离发送dense列表，保留owner/CAS/公开和最终全量验证，并用实际传输修改测试替换私有对象篡改测试。它将另审、另验、另清理、另提交，不能复用本批结果冒充新实现。材料整理随后独立进行，W2仍在R15之后。

## 清理与提交边界

本批唯一自有根 C:/Users/18221/AppData/Local/Temp/codeplus-w1-batch2-leader-20260923 含CPU/CUDA环境、自有cache、产物、tokenizer、真实/CPU测试数据和helper；另有上述专属Docker项目。新独立清理会话执行实际清理，Leader核对后精确本地提交，无push。正式失败/验证报告及三份batch1提交后记录保留入库。

R14四文件helper目录、batch1单个12511-byte finalizer脚本的删除各自被自动审批以 blocked by policy 拒绝，未执行、无更具体理由；两处针对性用户问题尚待回答，不由本批清理代删。收敛任务v3资源报告列25个已完成CPU测试目录、旧10个被拒目录、新2个被拒单文件及仍用于W2的环境/缓存/备份；均不属于本批清理范围。v3为8060 bytes，SHA256 5965d8634f0550901debd878391132f5b96c08b1316da1b8d8ee3264c14f9cce；未做后代链接审计，不宣称全部临时资源已清除。

R00–R26总目标继续，R15–R26未以本轮收敛替代验收。

## 独立清理与 Leader 复核结果

全新 /root/w1_batch2_cleanup 完成主清理并 STOP_WRITE。实际删除78139普通文件、16021目录、1022内部reparse节点和2个内部硬链接组，共4932179248逻辑字节；本批3容器/3卷/1网络已删除，19542/9103关闭，未杀进程。清理前后全部保护组匹配；Leader又独立核对74 reviewed、322 tested、206 production、6553 protected、199 previous、3 postnotes、8 SQL、7 spec和411 ignored，其中3目录实际存在。剩余2容器/5卷/4网络的身份及稳定定义相等，6个登记worker PID均已不存在。

严格比较有两项原FAIL保留：1052个自有普通目录仅attributes从0x10000010变为0x10；5724个既有global uv/cache普通目录仅size变化。全量差异、其他字段/节点、链接/硬链接、当前进程和Docker引用独立复核后，作为来源未知的metadata观察接纳本批原边界清理；没有重写属性或声称严格全等。共享after与最后predelete-v3完整172661行逐字段相等；after与最初before仍只有上述5724条size差异。Leader独立完整解压/解析两组gzip及95182个自有节点，得到同样结果，不冒称所有共享文件内容SHA已核对。辅助审计最初spec相对路径错误、报告脚本括号SyntaxError均有独立失败记录，仅修helper。

最后精确删除 C:/Users/18221/AppData/Local/Temp/codeplus-w1-batch2-cleanup-20260923 被auto-review CreateProcess拒绝，唯一原因blocked by policy，整条命令和inline checks均未执行。该新目录12普通文件63232 bytes实际保留；fresh独立检查七层祖先无reparse、文件nlink1且SHA/大小等inventory。没有重试/换工具/代理删除，已只针对这个目录发起用户重试或保留问题，尚无答复。主资源清理证据不因这一残留而改写，整批清理不得标完全PASS。之前R14/batch1拒绝范围和bf43全部资源仍保护。

正式33份清理附件均由Leader逐项重核raw SHA/bytes；manifest SHA cafcece17c29e0c4770e5dd12b7366a293aa3f6c3991ef683fc88ee0d2a3d38f。summary SHA 922d3ae4f7323d7da3f91b2384f0545488be1b874929727cde4e8c6bd4ad173a，after d0191d00fdfe45cd65bdd10599660a38ecee67f99c7e69ce44201cf8ea9fb66c，finalizer 24971fd4abbc4b8c706731afaf8c01e823c84638b3c810d45d5b79ac253ee76c。Leader读回见W1-batch2-leader-postcleanup.json。

收敛任务其后v4资源报告9042 bytes/SHA03a75a0d423615e822a3be17d0eaedc5ce637f6255988f6d144be3c3311da207已原字节附入；记26个测试目录和4个被拒单文件。后又收到第5个单文件convergence-sync.py被拒通知，仍在本批范围之外；旧报告不覆写，后续正式资源版本待该任务实际同步后更新。本地提交只收已验代码和事实记录，辅助目录删除继续等待各自用户指示，整个R00–R26任务不结项。
