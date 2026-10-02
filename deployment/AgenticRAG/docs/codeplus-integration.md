# 知识功能使用

先配置 `knowledge_development_config`，见[配置说明](domain-and-configuration.md)。知识工具接入普通 Agent，问答继续使用当前回答模型、文件工具、权限和会话能力。

## 选择与查看知识库

```text
/knowledge create 我的资料
/knowledge use <库UUID>
/knowledge status
/knowledge sources
/knowledge sources --revision <版本UUID>
/knowledge off
```

create/use 选择知识库；status 查看发布版及待处理操作；sources 查看文档成员；off 取消当前会话的知识库选择。导入、更新和删除见[文档更新](ordinary-mutations.md)。

## 问答与报告

```text
/knowledge ask --mode auto 比较这些资料的共同结论
/knowledge report --output "D:\Reports\报告.md" 整理资料并保存报告
/knowledge continue --run <运行UUID> 补查未解决的问题
codeplus -p "问题" --knowledge-library <库UUID> --knowledge-mode fixed
```

report 由普通文件工具保存；continue 可沿当前会话继续，也可指定历史运行，并支持 `--output`。CLI 对应参数为 `--knowledge-report <路径>`、`--knowledge-continue <运行UUID>`。每轮使用当前发布版，策略见[检索](retrieval.md)。

## 来源同步

```text
/knowledge watch "D:\资料"
/knowledge watch
/knowledge sync
/knowledge unwatch "D:\资料"
```

watch 保存文件或目录订阅，无参数时显示订阅及最近结果。TUI/Remote 运行期间自动同步；CLI 使用 sync 核对一次。

Windows 使用文件通知，合并 600ms 内变化，每 30 秒核对一次；其他系统按周期核对。仅处理 Markdown/TXT，内容 hash 未变时跳过。新增和修改走普通导入，失败保留上次成功版本；删除源文件不自动删库，改名默认新增。

unwatch 停止指定订阅，off 只取消会话选库。宿主退出时停止监听；订阅目录须与知识库数据目录分开。

中断操作见[恢复](manual-recovery.md)，更换模型见[模型切换](model-switching.md)，历史原文见[引用查看](sources-and-evidence.md)。
