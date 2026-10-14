# 组件复用与验证

## 模型接口


`tests/test_model_management.py` 使用独立临时数据和 HTTP 替身，检查协议、工具签名、列表分页、管理操作、配置升级、用途选择、嵌入、语音、重排、密钥轮换和错误展示。另有旧功能接线与请求前缀检查。

协议参考 [Claude Messages](https://platform.claude.com/docs/en/api/messages/create)、[Gemini generateContent](https://ai.google.dev/api/generate-content)、[OpenAI Responses](https://developers.openai.com/api/reference/responses/overview)。


## 主动回忆适配


`role_system/active_memory/core.py` 只依赖 Python 标准库，`__init__.py` 只导出核心数据类型。其他宿主可复用这些文件，提供自己的来源适配器。

宿主创建 `Context(account, role, session, now, timezone)`，将记忆转换成 `Memory`，解析人物与时间后创建 `Cues`。`candidates` 完成边界与线索筛选；可选语义过滤在此后缩小候选集合；`select` 和 `render` 仅从剩余集合取结果。宿主通过自己公开的工具接口绑定调用；`Context` 不接受模型提供的权限参数。

本宿主的具体数据库映射在 `adapter.py`，工具入口、设置和可选模型调用在 `service.py`。其他系统需要按自己的记忆字段编写适配器，不能把缺失的归属默认成公共世界记忆。

角色工具使用说明：

> 我想主动回忆过去的人物、事情、时间或感受时，可以使用这个工具。我可以只给出记得的线索；我保留结果的归属与来源，是否提起由我决定。



## 人格组件


独立组件位于 `role_system/personality`：存储、设置、经历学习、背景选择、编排及管理接口分别维护。生产数据位于实例的 `personality/`，不进入插件源码或干净安装包。

基础验证覆盖关闭兼容、前后台共用、人物/账号/角色隔离、私密范围与公开提及、重复证据、跨日期支持、相反证据、原话更正、冻结、修订恢复、管理操作与在途请求、数据交换、清空，以及原有发送/插件/正则/回执流程。测试使用可控模型和隔离数据库，不向 QQ 投递测试消息。

机制正确不等于真人感必然改善。需要通过后续真实交流，观察回应是否自然、事实是否准确、个性是否稳定及认识是否合理；不能用更长、更热情或更多内心独白替代这些评价。

参考：[PersonaForge](https://aclanthology.org/2026.findings-acl.386/) 与 [Dynamic Persona Coherence](https://aclanthology.org/2026.acl-long.1336/)。借鉴分层、选择性处理和经历修订的思路，不宣称复现整篇论文。
