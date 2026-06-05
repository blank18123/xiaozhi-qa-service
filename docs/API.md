# XiaoZhi QA Service API 文档

Base URL: `http://localhost:8010`

---

## 1. 文本问答

### POST /api/v1/ask

输入文本，流式返回 LLM 回复。

**Request**

```http
POST /api/v1/ask HTTP/1.1
Host: localhost:8010
Content-Type: application/json

{
    "query": "今天天气怎么样？",
    "mode": "text",
    "session_id": null,
    "role_prompt": null
}
```

**参数说明**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| query | string | 是 | 用户输入文本 |
| mode | string | 是 | 固定为 `"text"` |
| session_id | string | 否 | 会话ID（不传自动生成，多轮对话需传入相同ID） |
| role_prompt | string | 否 | 覆盖默认系统提示词 |

**Response** — `text/event-stream` (SSE)

```
data: {"event": "session_id", "data": "abc123def456", "tool_name": null}

data: {"event": "token", "data": "今天", "tool_name": null}
data: {"event": "token", "data": "天气", "tool_name": null}
data: {"event": "token", "data": "不错", "tool_name": null}

data: {"event": "done", "data": "abc123def456", "tool_name": null}
```

**SSE 事件类型**

| event | 含义 |
|-------|------|
| `session_id` | 返回会话ID（首个事件） |
| `token` | LLM 流式输出的文本片段 |
| `tool_call` | 触发了工具调用，`data` 为参数 JSON |
| `done` | 问答结束 |
| `error` | 错误信息 |

**curl 示例**

```bash
curl -X POST http://localhost:8010/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "你好，请介绍一下你自己", "mode": "text"}'
```

**Python 示例**

```python
import requests
import json

url = "http://localhost:8010/api/v1/ask"
payload = {"query": "北京到上海多远？", "mode": "text"}

with requests.post(url, json=payload, stream=True) as r:
    for line in r.iter_lines():
        if line:
            line = line.decode("utf-8")
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if event["event"] == "token":
                    print(event["data"], end="", flush=True)
                elif event["event"] == "done":
                    print()
```

---

## 2. 语音问答

### POST /api/v1/ask/audio

上传音频文件，返回 TTS 合成的语音回复。

**Request** — `multipart/form-data`

```http
POST /api/v1/ask/audio HTTP/1.1
Host: localhost:8010
Content-Type: multipart/form-data; boundary=----boundary

------boundary
Content-Disposition: form-data; name="file"; filename="question.wav"
Content-Type: audio/wav

<audio binary data>
------boundary
Content-Disposition: form-data; name="session_id"

abc123
------boundary--
```

**参数说明**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| file | file | 是 | 音频文件（WAV/MP3，推荐 16kHz 单声道 WAV） |
| session_id | string | 否 | 会话ID |

**Response** — `audio/wav`

返回 WAV 格式音频，可直接播放或保存。

**curl 示例**

```bash
curl -X POST http://localhost:8010/api/v1/ask/audio \
  -F "file=@question.wav" \
  -F "session_id=abc123" \
  -o answer.wav
```

---

## 3. 错误响应

| HTTP 状态码 | 含义 |
|:----------:|------|
| 200 | 成功 |
| 400 | 请求参数错误（如 mode 不匹配、无音频回复） |
| 500 | 服务内部错误（LLM/TTS/ASR 调用失败） |

---

## 4. 多轮对话

传入相同的 `session_id` 即可保持上下文：

```bash
# 第一轮
curl -X POST http://localhost:8010/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "我叫小明", "mode": "text", "session_id": "my-session"}'

# 第二轮（记住上下文）
curl -X POST http://localhost:8010/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "我叫什么名字？", "mode": "text", "session_id": "my-session"}'
```

---

## 5. 工具调用示例

当 LLM 触发 Function Call 时，SSE 流中会出现 `tool_call` 事件：

```
data: {"event":"tool_call","data":"{\"city\": \"北京\"}","tool_name":"get_weather"}
data: {"event":"token","data":"北京今天晴，25°C","tool_name":null}
```

可用工具：天气查询、新闻获取、时间查询、音乐播放、RAGFlow 知识检索、联网搜索、HomeAssistant 智能家居等。
