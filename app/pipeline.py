import json
import uuid
from typing import AsyncGenerator, Dict, Any, Optional
from app.config.logger import setup_logging
from app.core.utils.dialogue import Message, Dialogue
from app.core.providers.tools.unified_tool_handler import UnifiedToolHandler
from app.core.utils.prompt_manager import PromptManager
from plugins_func.loadplugins import auto_import_modules

auto_import_modules("plugins_func.functions")

TAG = __name__

DIRECT_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "direct_answer",
        "description": "当用户请求不匹配其他任何工具时，用此选项直接回复",
        "parameters": {
            "type": "object",
            "properties": {
                "response": {"type": "string", "description": "回复用户的完整内容"},
            },
            "required": ["response"],
        },
    },
}


class VoiceAssistantPipeline:
    def __init__(
        self,
        llm,
        tts=None,
        asr=None,
        memory=None,
        intent=None,
        intent_type: str = "nointent",
        config: Dict[str, Any] = None,
    ):
        self.llm = llm
        self.tts = tts
        self.asr = asr
        self.memory = memory
        self.intent = intent
        self.intent_type = intent_type
        self.config = config or {}
        self.logger = setup_logging()

        self.func_handler = UnifiedToolHandler(type('ConnStub', (), {'config': self.config})())
        self.prompt_manager = PromptManager(self.config, self.logger)
        self._session_dialogues: Dict[str, Dialogue] = {}

    def _get_dialogue(self, session_id: str) -> Dialogue:
        if session_id not in self._session_dialogues:
            dialogue = Dialogue()
            system_prompt = self.prompt_manager.get_quick_prompt("")
            dialogue.put(Message(role="system", content=system_prompt))
            self._session_dialogues[session_id] = dialogue
        return self._session_dialogues[session_id]

    def reset(self, session_id: str):
        self._session_dialogues.pop(session_id, None)

    async def ask_text(
        self, query: str, session_id: str = None, role_prompt: str = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not session_id:
            session_id = uuid.uuid4().hex

        dialogue = self._get_dialogue(session_id)
        if role_prompt:
            dialogue.update_system_message(role_prompt)

        dialogue.put(Message(role="user", content=query))
        yield {"event": "session_id", "data": session_id}

        try:
            functions = None
            if self.intent_type == "function_call" and hasattr(self, "func_handler"):
                functions = list(self.func_handler.get_functions())
                if functions:
                    functions.append(DIRECT_ANSWER_TOOL)

            if functions:
                llm_stream = self.llm.response_with_functions(
                    session_id,
                    dialogue.get_llm_dialogue(),
                    functions=functions,
                )
            else:
                llm_stream = ((token, None) for token in self.llm.response(
                    session_id,
                    dialogue.get_llm_dialogue(),
                ))

            full_text = ""
            pending_tool_calls = {}

            for token, tool_call in llm_stream:
                if tool_call:
                    tool_id = tool_call.get("id", "")
                    name = tool_call.get("function", {}).get("name", "")
                    if name == "direct_answer":
                        args = tool_call.get("function", {}).get("arguments", "{}")
                        try:
                            data = json.loads(args) if isinstance(args, str) else args
                            direct_text = data.get("response", "")
                        except (json.JSONDecodeError, TypeError):
                            direct_text = ""
                        if direct_text:
                            full_text += direct_text
                            yield {"event": "token", "data": direct_text}
                        continue
                    pending_tool_calls[tool_id] = tool_call
                    yield {
                        "event": "tool_call",
                        "tool_name": name,
                        "data": tool_call.get("function", {}).get("arguments", ""),
                    }
                elif token:
                    full_text += token
                    yield {"event": "token", "data": token}

            if pending_tool_calls:
                tool_results = await self._execute_tools(pending_tool_calls)
                for result in tool_results:
                    dialogue.put(Message(role="tool", content=json.dumps(result)))
                async for event in self._continue_with_tool_results(
                    session_id, dialogue, depth=1
                ):
                    yield event

            if full_text:
                dialogue.put(Message(role="assistant", content=full_text))

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"ask_text error: {e}")
            yield {"event": "error", "data": str(e)}

        yield {"event": "done", "data": session_id}

    async def ask_audio(
        self, audio_bytes: bytes, session_id: str = None
    ) -> AsyncGenerator[bytes, None]:
        if not session_id:
            session_id = uuid.uuid4().hex

        if not self.asr:
            raise RuntimeError("ASR module not configured")

        # ASR: speech_to_text_wrapper expects List[bytes] + session_id
        text, _ = await self.asr.speech_to_text_wrapper(
            [audio_bytes], session_id, audio_format="pcm"
        )
        if not text:
            return

        answer = ""
        async for event in self.ask_text(text, session_id):
            if event["event"] == "token":
                answer += event["data"]
            elif event["event"] == "done":
                break

        if answer and self.tts:
            audio_data = await self.tts.synthesize(answer)
            yield audio_data

    async def _execute_tools(self, pending_tool_calls: dict) -> list:
        results = []
        for tool_id, tc in pending_tool_calls.items():
            name = tc.get("function", {}).get("name", "")
            args_str = tc.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except json.JSONDecodeError:
                args = {}

            try:
                result = self.func_handler.execute(name, args)
                results.append({
                    "tool_call_id": tool_id,
                    "role": "tool",
                    "content": str(result) if result else "",
                })
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"Tool {name} error: {e}")
                results.append({
                    "tool_call_id": tool_id,
                    "role": "tool",
                    "content": f"工具执行失败: {e}",
                })
        return results

    async def _continue_with_tool_results(
        self, session_id: str, dialogue: Dialogue, depth: int = 1
    ) -> AsyncGenerator[Dict[str, Any], None]:
        MAX_DEPTH = 3
        if depth >= MAX_DEPTH:
            dialogue.put(Message(
                role="user",
                content="[系统]已达到最大工具调用次数，请基于已有信息直接回答。",
            ))

        functions = list(self.func_handler.get_functions())
        llm_stream = self.llm.response_with_functions(
            session_id, dialogue.get_llm_dialogue(), functions=functions,
        )

        full_text = ""
        pending = {}

        for token, tc in llm_stream:
            if tc:
                name = tc.get("function", {}).get("name", "")
                if name == "direct_answer":
                    args = tc.get("function", {}).get("arguments", "{}")
                    try:
                        data = json.loads(args) if isinstance(args, str) else args
                        direct_text = data.get("response", "")
                    except (json.JSONDecodeError, TypeError):
                        direct_text = ""
                    if direct_text:
                        full_text += direct_text
                        yield {"event": "token", "data": direct_text}
                    continue
                pending[tc.get("id", "")] = tc
                yield {
                    "event": "tool_call",
                    "tool_name": name,
                    "data": tc.get("function", {}).get("arguments", ""),
                }
            elif token:
                full_text += token
                yield {"event": "token", "data": token}

        if pending:
            results = await self._execute_tools(pending)
            for r in results:
                dialogue.put(Message(role="tool", content=r["content"]))
            async for event in self._continue_with_tool_results(
                session_id, dialogue, depth + 1
            ):
                yield event

        if full_text:
            dialogue.put(Message(role="assistant", content=full_text))
