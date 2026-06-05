from typing import Dict, Any
from app.config.logger import setup_logging
from app.core.utils import tts, llm, intent, memory, vad, asr as asr_utils

TAG = __name__
logger = setup_logging()


def initialize_modules(
    logger_obj,
    config: Dict[str, Any],
    init_vad=False,
    init_asr=False,
    init_llm=False,
    init_tts=False,
    init_memory=False,
    init_intent=False,
) -> Dict[str, Any]:
    modules = {}

    if init_tts and "TTS" in config.get("selected_module", {}):
        sel = config["selected_module"]["TTS"]
        tts_type = config.get("TTS", {}).get(sel, {}).get("type", sel)
        modules["tts"] = tts.create_instance(
            tts_type,
            config["TTS"][sel],
            str(config.get("delete_audio", True)).lower() in ("true", "1", "yes"),
        )
        logger_obj.bind(tag=TAG).info(f"TTS: {sel}")

    if init_llm and "LLM" in config.get("selected_module", {}):
        sel = config["selected_module"]["LLM"]
        llm_type = config.get("LLM", {}).get(sel, {}).get("type", sel)
        modules["llm"] = llm.create_instance(llm_type, config["LLM"][sel])
        logger_obj.bind(tag=TAG).info(f"LLM: {sel}")

    if init_asr and "ASR" in config.get("selected_module", {}):
        sel = config["selected_module"]["ASR"]
        asr_type = config.get("ASR", {}).get(sel, {}).get("type", sel)
        modules["asr"] = asr_utils.create_instance(
            asr_type,
            config["ASR"][sel],
            str(config.get("delete_audio", True)).lower() in ("true", "1", "yes"),
        )
        logger_obj.bind(tag=TAG).info(f"ASR: {sel}")

    if init_memory and "Memory" in config.get("selected_module", {}):
        sel = config["selected_module"]["Memory"]
        mem_type = config.get("Memory", {}).get(sel, {}).get("type", sel)
        modules["memory"] = memory.create_instance(
            mem_type, config["Memory"][sel], config.get("summaryMemory")
        )
        logger_obj.bind(tag=TAG).info(f"Memory: {sel}")

    if init_intent and "Intent" in config.get("selected_module", {}):
        sel = config["selected_module"]["Intent"]
        intent_type = config.get("Intent", {}).get(sel, {}).get("type", sel)
        modules["intent"] = intent.create_instance(intent_type, config["Intent"][sel])
        logger_obj.bind(tag=TAG).info(f"Intent: {sel}")

    return modules
