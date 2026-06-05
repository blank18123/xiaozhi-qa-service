# XiaoZhi QA Service

独立语音问答服务 — 从小智AI（xiaozhi-server）解耦，通过 FastAPI 暴露标准 REST API，支持文本/语音问答。

## 系统架构

```
         ┌──────────────────────────────────────────┐
         │              Clients                     │
         │    curl / Postman / Web / 第三方服务       │
         └──────┬──────────────┬────────────────────┘
                │ POST /ask    │ POST /ask/audio
                ▼              ▼
         ┌──────────────────────────────────────────┐
         │          FastAPI (:8010)                  │
         │   /api/v1/ask       /api/v1/ask/audio    │
         └──────────┬───────────────────────────────┘
                    │
                    ▼
         ┌──────────────────────────────────────────┐
         │       VoiceAssistantPipeline              │
         │                                          │
         │  Dialogue ──► LLM ──► Intent ──► Tools   │
         │     │                                │    │
         │     └── Memory              Tool Handler │
         │                                          │
         │  ASR ──► text ──► LLM ──► TTS ──► audio │
         └──────┬───────────────────────┬───────────┘
                │                       │
                ▼                       ▼
         ┌──────────┐          ┌──────────────┐
         │ Provider │          │   Provider    │
         │   Layer  │          │    Layer      │
         │          │          │               │
         │ LLM x9   │          │ TTS x18       │
         │ ASR x14  │          │ Memory x4     │
         │          │          │ Intent x3     │
         └──────────┘          └──────────────┘
```

## 快速开始

### 环境要求
- Python 3.10+
- pip

### 1. 安装依赖
```bash
cd xiaozhi-qa-service
pip install -r requirements.txt
```

### 2. 配置
编辑 `data/.config.yaml`，填入你的 API Key：
```yaml
selected_module:
  TTS: EdgeTTS
  LLM: DeepSeekLLM
  ASR: FunASR
  Memory: nomem
  Intent: nointent

LLM:
  DeepSeekLLM:
    type: openai
    api_key: "sk-your-key-here"
    url: https://api.deepseek.com
    model_name: "deepseek-chat"

TTS:
  EdgeTTS:
    type: edge

ASR:
  FunASR:
    type: fun_server
```

### 3. 启动
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

### 4. 验证
浏览器打开 http://localhost:8010/docs 查看 Swagger 文档。

## Docker 部署
```bash
docker build -t xiaozhi-qa-service .
docker run -p 8010:8010 -v $(pwd)/data:/app/data xiaozhi-qa-service
```

## API 概览
| 端点 | 方法 | 说明 |
|------|------|------|
| /api/v1/ask | POST | 文本问答（SSE 流式） |
| /api/v1/ask/audio | POST | 语音问答（上传音频，返回音频） |
| /docs | GET | Swagger UI |
| /redoc | GET | ReDoc |

详细文档见 docs/API.md

## 目录结构
```
xiaozhi-qa-service/
├── app/
│   ├── main.py              FastAPI 入口
│   ├── pipeline.py          核心问答管线
│   ├── api/routes.py        /ask 路由
│   ├── schemas/ask.py       Pydantic 模型
│   ├── config/              配置/日志/校验
│   ├── core/providers/      LLM/TTS/ASR/Memory/Intent/Tools
│   ├── core/utils/          工具函数
│   └── plugins_func/        插件/Function Call
├── data/.config.yaml        用户配置
├── config.yaml              默认配置
├── docs/API.md              接口文档
├── postman/                 Postman Collection
├── requirements.txt
├── Dockerfile
└── README.md
```

## 可用 Provider

### LLM (9种)
openai · gemini · ollama · coze · dify · fastgpt · xinference · AliBL · homeassistant

### TTS (18种)
edge · doubao · aliyun · xunfei · tencent · siliconflow · openai · gpt_sovits_v2/v3 · fishspeech · paddle_speech · minimax · huoshan · index_stream · cozecn · custom

### ASR (14种)
fun_local · fun_server · aliyun · doubao · baidu · tencent · xunfei · openai · qwen3_asr_flash · sherpa_onnx_local · vosk

### Memory (4种)
nomem · mem_local_short · mem_report_only · mem0ai

### Intent (3种)
nointent · function_call · intent_llm
