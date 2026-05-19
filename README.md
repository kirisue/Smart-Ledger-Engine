这是一个泛用型的智能记录与对账引擎。最初诞生于日常记账，但现已演变为一套高度模块化、配置驱动（Configuration-Driven）的通用数据记录系统。

它不仅仅是一个记账工具，更是一套具备AI语音识别、本地加密存储、自定义导出模板的自动化 workflow 引擎。

🚀 核心特点
零硬编码 (Zero Hardcoding)：通过 preset.json 配置文件实现业务逻辑与核心代码完全解耦。无论是记账、战场记录还是其他业务，只需修改 JSON 模板，无需修改一行 Python 代码。

AI 语音助手：集成 Whisper 语音识别（Whisper）与 Edge-TTS 语音合成，支持 PTT（按下说话）模式，实现双手脱离键盘的快速记录。

深层解耦的导出机制：支持自定义导出模板，通过 JSON 占位符引擎，可输出任何你需要的复杂报表格式。

隐私优先 (Privacy First)：所有数据（SQLite 数据库）与配置文件均存储在本地。支持 XOR+Base64 加密，确保核心配置不被篡改或泄露。

动态彩蛋系统：内置连击统计与语音彩蛋反馈，增加人机交互的趣味性。

🛠️ 架构设计 (Why it's special)
传统的记账软件通常将逻辑写死在代码里，而本引擎的核心逻辑是“配置注入灵魂”：

hl.py (Engine): 负责 GUI 渲染、SQLite 交互、语音处理、加密逻辑。

preset.json (Template): 负责业务术语映射（Terms）、导出模板（Export Templates）、UI 场景配置。

这意味着你可以把这一套代码复制到任何业务场景中，通过分发不同的 JSON 模板，瞬间变身成完全不同的行业工具。

📥 快速开始
环境依赖
请确保你的电脑已安装 Python 3.9+，并安装了必要的音频解码库：

Bash
# 安装必要依赖
pip install -r requirements.txt
注意：Whisper 大脑需要 FFmpeg 支持，请确保系统 PATH 中已配置 ffmpeg 或将 ffmpeg.exe 放入软件同级目录。

使用方法
下载最新 Release 版本。

确保 ledger.db 与 hl.py (或 .exe) 位于同一文件夹。

首次运行会自动生成默认配置，你可以通过菜单 工具/设置 -> 导入业务模板 加载你定制的 .json 文件。

⚙️ 配置文件 (preset.json) 说明
所有业务相关配置均在 preset.json 中：

JSON
{
  "export_templates": {
    "flow_in": "{time} 赚 [{team}]/[{name}]，备注: {remark}",
    "flow_out": "{time} 亏 [{team}]/[{name}]，备注: {remark}"
  },
  "terms": {
    "entity": "名称",
    "tag": "项目标签",
    "flow_in": "流入词汇",
    "flow_out": "流出词汇"
  }
}
📝 开发计划 & 贡献
本项目目前处于开源初期，欢迎提交 Issue 和 Pull Request。

当前版本: v1.0.0

主要开发者: Kirisu

⚖️ 免责声明
本软件仅作为个人数据记录辅助工具，使用 AI 接口（如 DeepSeek API）时产生的费用与风险由用户自行承担。请妥善保存本地数据库文件，它是你的唯一数据来源。