# R06 本地存储与并发契约

本文前半保留 R06–R08 的 schema3 基础实现说明。当前开发包 0.1.0 追加到 schema9；R10/R13 提供正式索引与发布，R14 提供恢复，R15 的运行保护、实际 reader 和自动物理回收见 [索引生命周期](index-lifetimes.md)。基础测试中的合成 READY 夹具不代表真实 Milvus 验收，各阶段实际执行证据独立记录。

## 运行时与数据库

核心额外依赖固定 APSW 3.53.4.0，Windows CPython 3.14 x64 wheel 在本机安装成功，实际 SQLite 为 3.53.4。源码启动校验实际 SQLite >=3.51.3，不回退到 stdlib sqlite3。本机标准库仍为 3.50.4：官方确认 WAL reset 并发缺陷影响 3.7.0–3.51.2，3.51.3 及之后修复；早期维护分支另有 3.50.7/3.44.6 回移，本包选择统一新版本门槛。[SQLite WAL reset](https://www.sqlite.org/wal.html#walresetbug)、[3.51.3 release](https://www.sqlite.org/releaselog/3_51_3.html)、[APSW 安装](https://rogerbinns.github.io/apsw/install.html)、[锁定发行](https://pypi.org/project/apsw/3.53.4.0/)。

所有连接启用 foreign_keys、WAL、synchronous=FULL。读操作使用短读事务，修改使用 BEGIN IMMEDIATE；SQLite busy timeout 默认 1500ms，允许显式 1–10000ms，没有无限重试。WAL 仍只有一个写者，OS 业务锁和 SQLite 写事务不是同一把锁。归档读写、解析、推理、等待在事务外。APSW 错误保留类型和 stage，busy 转为 LIBRARY_BUSY；不删除 WAL/SHM，也不把它们当作可清理临时文件。

schema.sql 是实际发行资源，通过 importlib.resources 读取。初始化由稳定 schema OS 锁协调，以一个事务写入关系表、store_id、application_id、user_version 与 migration SHA256/时间。重开校验应用身份、版本和迁移指纹；未知新版本、非本应用库或目录／库身份不符直接失败，不试图降级。schema 1 是新库初始迁移，不支持旧 CodePlus 格式。

R07 保持 schema.sql 的 migration 1 身份和字节；新增 inputs.sql migration 2。初始化/升级先验证原 migration 1 指纹、application/store 身份，再在单一短事务新增 input_manifests、input_items、input_results 和 document_sources 及不可变触发器，登记 migration 2 指纹并提升 user_version。真实 schema1 数据库的历史原件/版本/成员/快照及待恢复批次升级读回已纳入正式测试。不会重建数据库，也不会给旧 batch 凭 hash 补造输入清单。

R08 保持前两份 SQL 原字节，追加 processing.sql migration 3；验证旧迁移后才创建 processing_items 和完成结构封闭触发器。实际 schema1→3/schema2→3 升级保留旧历史，旧迁移 hash 异常时不创建新表。生产解析路径不用分别提交的 add_version/add_structure：归档核验和序列化完成后，由一个 owner/epoch 事务登记真实 version、所有结构与 ImportItem 完整检查点。input_results 仍是独立不可变 raw 事实。细节见 [解析与来源映射](parsing-and-source-maps.md)。

库、文档、文档版本、章节、Chunk、处理快照、revision 成员、批次、依赖、run 与 pin 的身份／状态都是关系字段。复合外键约束 kb→document→version→section 和 revision/member/snapshot/batch/run 的归属。配置、来源描述、heading path、spans、usage 的嵌套值使用 JSON；它们不代替关系身份与生命周期列。文档版本、快照、章节、Chunk、成员不能更新／删除；版本一旦被候选或历史 revision 引用，禁止再追加章节／Chunk，防止历史结构集合漂移。

## 本机数据目录

`Catalog(absolute_path, busy_timeout_ms=1500)` 不读取 cwd、环境配置或宿主。只创建新目录或认领已有空目录；非空目录必须已有合法 `.agentic-rag.json` 归属标记，内含随机 store_id。SQLite 保存相同 ID。每次连接和归档操作核验目录身份，路径所有已有分量拒绝 symlink、Windows reparse point、特殊文件与文件硬链接。权限错误直接失败；目录不能由其他程序并发替换，此机制不是对恶意本机同用户进程的安全沙箱。

Windows 要求真实本机绝对路径和 GetDriveTypeW=DRIVE_FIXED；拒绝 UNC、映射网络盘、相对盘符、Linux 风格路径、`..`、备用流和 junction。Linux 分支通过最长匹配 `/proc/self/mountinfo` 保守只接受 ext2/3/4、xfs、btrfs、f2fs、zfs；未知挂载、FUSE、overlay、tmpfs 和网络文件系统明确拒绝。这是当前保守边界，Linux 实际安装与运行不在本阶段验收范围，不以 wheel 存在声称已验收。

当前按需建立 catalog.sqlite、archives、staging、locks。宿主应传用户独立数据目录，不传源码、包安装或模型缓存目录；不扫描其他应用目录。初次归属标记写入中断留下不完整标记时保守拒绝，需明确诊断／人工处理，不能自动认领其内容。

## 不可变归档

`catalog.archives.put(binary_stream, expected_hash=...)` 逐块完整写入同 data_dir 的 staging 临时文件，计算 SHA256、flush/fsync，再读回验证。最终路径为 `archives/<hash前2位>/<完整hash>`。相同 hash 的短 OS 锁序列化完成步骤：已存在则重新验证、复用；损坏则明确失败，不覆盖历史。用途由 document_versions 的 raw_hash、parsed_hash、source_map_hash 列明确区分，同字节可共用对象。

Windows 同卷完成使用 MoveFileExW(MOVEFILE_WRITE_THROUGH)，不设 COPY_ALLOWED／REPLACE_EXISTING；意外已有目标时 OS 也会拒绝覆盖。Linux 在同 hash 锁下 rename，随后 fsync 目标目录和 archives 根目录。Windows 先 fsync 文件并请求 write-through 移动；Python 没有通用 Windows 目录 fsync，因此不把此流程描述为所有硬件／驱动下的断电证明。[MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)、[Python os.fsync](https://docs.python.org/3.14/library/os.html#os.fsync)。

`add_version` 先在事务外验证三个完整最终对象，再在拥有者短事务中同时写 archive_objects 引用与文档版本。不存在、不完整、hash 或已登记大小不符均失败。受控异常清理本次临时文件；进程死亡允许留未引用碎片或完整孤儿，本项不自动 GC。文件 fsync/完成失败的注入测试不等于真实磁盘满、断电；实际进程 terminate 证明进程死亡／OS 锁释放与已提交元数据回读，不能证明硬件掉电持久性。FULL 是所选 SQLite 提交策略，不把 NORMAL 解释为同等保证。

## 库锁、拥有者和手动恢复

`begin_mutation(kb_id, snapshot, manifest_hash)` 先非阻塞获取 `library-<UUID>.lock`，再检查该库无 pending batch，保存完整处理快照、基准版与批次，递增库的 owner_epoch 并生成 owner_nonce。返回 Mutation 持有 OS 文件句柄；owner token 同时含 store/kb/batch/nonce/epoch。Windows 使用 msvcrt.LK_NBLCK 锁 offset=0 的一个字节，Linux 使用 flock；文件描述符不可继承，跨进程需重新打开 Catalog，锁文件从不删除。[msvcrt.locking](https://docs.python.org/3.14/library/msvcrt.html#msvcrt.locking)。

R07 `begin_import(kb_id,snapshot,manifest)` 复用同一 owner 协议，在批次事务同时写完整请求清单和文档身份/基准 member 关联；遍历/路径API/原件IO均在该事务之外。request hash 固定，捕获结果后续逐项原子接纳、终态不可覆盖。完整 raw 引用无需也不会伪造 parsed/source_map 版本；R08 在实际解析完成后使用完整检查点事务登记版本和结构。来源更新和历史关联也由同 owner 短事务写入。详见 [输入快照](input-snapshots.md)。

每个拥有者敏感写入都在短写事务内检查仍持有的锁句柄、锁路径身份、同一 Catalog、store/kb/batch、nonce/epoch、库高水位代次、pending pointer 和执行中状态。释放后的旧 lease 即使无人接管也不能写；异步产物调用方须把生成时的 token 通过 `produced_by=` 传入，不能用新 owner token 替换旧结果身份。不同库有独立业务锁；运行登记不取得库修改锁。

Mutation.close/context exit 对未完成批次登记 WAITING_RECOVERY，再释放锁；强杀无法写状态时，原批次仍 pending 并阻止新修改。`identify_interrupted(kb_id)` 只有取得同一 OS 锁后才能将执行中批次标为 WAITING_RECOVERY 并返回原 token。它不凭 PID／超时判断，更不自动继续。`resume_mutation(expected_token)` 再取锁，在事务内重验身份／状态，增加 epoch、换 nonce 并恢复原阶段。调用方需先取得明确继续意图并验证检查点；完整产品交互与检查点复用留 R14。

`Mutation.abandon()` 是手动原语：仍要活跃拥有者，事务内标 ABANDONED、清 pending、移除该批次索引依赖，再释放锁；不删档案、不删物理索引。持有 lease 的结果调用方使用 context/finally 关闭；不能期待 Python 析构器释放业务状态。schema／同 hash 完成锁有 1500ms 内有界等待，业务库锁和 run 锁立即返回忙。

## 候选、运行 pin 与后续边界

`add_candidate` 只登记 PREPARING revision 和完整成员，必须匹配当前批次基准版／快照；没有 `set_current_pointer`、无条件 READY 或发布接口。候选及 base 显式登记 revision_dependencies，`retain_revision(..., 'recovery'|'vector_reuse')` 在拥有者事务内拒绝 RECLAIMING/RECLAIMED。PREPARING/FAILED 可以登记恢复保护，这不使其可查询。R10/R13 必须在真实索引验证后加入唯一发布事务，核对 base 当前指针、owner 代次和候选验证资格。

`start_run(kb_id, resolved_config)` 先取得唯一 run OS 生命周期锁，再用同一个短写事务读取同库 current revision，核对 READY、编码身份、parent 同库，写 run 与 active pin。run 的 binding/config 不可改；pin 指向完整 revision，和是否已有命中 Chunk 无关。索引进入 RECLAIMING 后不能新绑定。正常 finish 必须是合法终态，run 结束与 pin release 在同事务中，提交后释放锁；未显式 finish 的 close 记录 consumer_closed。

`release_crashed_run(run_id, expected_nonce)` 先取得运行锁并持有至事务提交，在事务内复核 nonce、active 状态和 run，再标 failed／released。运行仍持锁或身份不符就保留 pin。未来 GC 必须在同一 SQLite 写事务资格边界检查 current、active pins、revision_dependencies 后才能 claim 回收；R06 没有自动扫描或物理删除入口，R15 实现该完整协议。

正式回归入口见 `tests/test_storage.py` 和 `tests/test_package_install.py`。
