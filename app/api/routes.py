import json
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, Response
from app.schemas.ask import AskRequest, AskMode
from app.pipeline import VoiceAssistantPipeline
from app.config.settings import load_config
from app.config.logger import setup_logging
from app.core.utils.modules_initialize import initialize_modules

router = APIRouter()
logger = setup_logging()
TAG = __name__

_pipeline: VoiceAssistantPipeline = None
_config: dict = None


def get_pipeline() -> VoiceAssistantPipeline:
    global _pipeline, _config
    if _pipeline is None:
        _config = load_config()
        modules = initialize_modules(
            logger,
            _config,
            init_vad=False,
            init_asr=("ASR" in _config.get("selected_module", {})),
            init_llm=("LLM" in _config.get("selected_module", {})),
            init_tts=("TTS" in _config.get("selected_module", {})),
            init_memory=("Memory" in _config.get("selected_module", {})),
            init_intent=("Intent" in _config.get("selected_module", {})),
        )
        llm_instance = modules.get("llm")
        asr_instance = modules.get("asr")
        tts_instance = modules.get("tts")
        memory_instance = modules.get("memory")
        intent_instance = modules.get("intent")
        intent_type = "nointent"
        if modules.get("intent"):
            sel = _config.get("selected_module", {}).get("Intent", "nointent")
            intent_type = _config.get("Intent", {}).get(sel, {}).get("type", sel)

        _pipeline = VoiceAssistantPipeline(
            llm=llm_instance,
            tts=tts_instance,
            asr=asr_instance,
            memory=memory_instance,
            intent=intent_instance,
            intent_type=intent_type,
            config=_config,
        )
        logger.bind(tag=TAG).info("Pipeline initialized")
    return _pipeline


@router.post("/ask")
async def ask(request: AskRequest):
    """文本问答 — JSON 请求，SSE 流式响应"""
    if request.mode != AskMode.text:
        raise HTTPException(400, "JSON 请求 mode 必须为 text")

    pipeline = get_pipeline()

    async def event_stream():
        async for event in pipeline.ask_text(
            query=request.query,
            session_id=request.session_id,
            role_prompt=request.role_prompt,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ask/audio")
async def ask_audio(
    file: UploadFile = File(...),
    session_id: str = Form(None),
):
    """语音问答 — multipart/form-data，返回 audio/wav"""
    pipeline = get_pipeline()
    audio_bytes = await file.read()

    tts_chunks = []
    async for chunk in pipeline.ask_audio(audio_bytes, session_id):
        tts_chunks.append(chunk)

    if not tts_chunks:
        raise HTTPException(400, "未能生成语音回复")

    return Response(content=b"".join(tts_chunks), media_type="audio/wav")
