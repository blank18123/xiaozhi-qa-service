import os
import io
import wave
import uuid
import json
import time
import queue
import shutil
import asyncio
import tempfile
import traceback
import threading
try:
    import opuslib_next
except Exception:
    opuslib_next = None

from abc import ABC, abstractmethod
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType
from core.utils.util import remove_punctuation_and_length
from typing import Optional, Tuple, List, NamedTuple


if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class ASRProviderBase(ABC):
    def __init__(self):
        pass

    # 鎵撳紑闊抽閫氶亾

        """灏哖CM鏁版嵁杞崲涓篧AV鏍煎紡"""
        if len(pcm_data) == 0:
            logger.bind(tag=TAG).warning("PCM鏁版嵁涓虹┖锛屾棤娉曡浆鎹AV")
            return b""

        # 纭繚鏁版嵁闀垮害鏄伓鏁帮紙16浣嶉煶棰戯級
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]

        # 鍒涘缓WAV鏂囦欢澶?
        wav_buffer = io.BytesIO()
        try:
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)  # 鍗曞０閬?
                wav_file.setsampwidth(2)  # 16浣?
                wav_file.setframerate(16000)  # 16kHz閲囨牱鐜?
                wav_file.writeframes(pcm_data)

            wav_buffer.seek(0)
            wav_data = wav_buffer.read()

            return wav_data
        except Exception as e:
            logger.bind(tag=TAG).error(f"WAV杞崲澶辫触: {e}")
            return b""

    class AudioArtifacts(NamedTuple):
        pcm_frames: List[bytes]
        """PCM闊抽甯у垪琛?""
        pcm_bytes: bytes
        """鍚堝苟鍚庣殑PCM闊抽瀛楄妭鏁版嵁"""
        file_path: Optional[str]
        """WAV鏂囦欢璺緞"""
        temp_path: Optional[str]
        """涓存椂WAV鏂囦欢璺緞"""

    def get_current_artifacts(self) -> Optional["ASRProviderBase.AudioArtifacts"]:
        return self._current_artifacts

    def requires_file(self) -> bool:
        """鏄惁闇€瑕佹枃浠惰緭鍏?""
        return False

    def prefers_temp_file(self) -> bool:
        """鏄惁浼樺厛浣跨敤涓存椂鏂囦欢"""
        return False

    def build_temp_file(self, pcm_bytes: bytes) -> Optional[str]:
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
            with wave.open(temp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(pcm_bytes)
            return temp_path
        except Exception as e:
            logger.bind(tag=TAG).error(f"涓存椂闊抽鏂囦欢鐢熸垚澶辫触: {e}")
            return None

    def save_audio_to_file(self, pcm_data: List[bytes], session_id: str) -> str:
        """PCM鏁版嵁淇濆瓨涓篧AV鏂囦欢"""
        module_name = __name__.split(".")[-1]
        file_name = f"asr_{module_name}_{session_id}_{uuid.uuid4()}.wav"
        file_path = os.path.join(self.output_dir, file_name)

        with wave.open(file_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 2 bytes = 16-bit
            wf.setframerate(16000)
            wf.writeframes(b"".join(pcm_data))

        return file_path

    async def speech_to_text_wrapper(
        self, opus_data: List[bytes], session_id: str, audio_format="opus"
    ) -> Tuple[Optional[str], Optional[str]]:
        file_path = None
        temp_path = None
        try:
            if audio_format == "pcm":
                pcm_data = opus_data
            else:
                pcm_data = self.decode_opus(opus_data)
            combined_pcm_data = b"".join(pcm_data)

            free_space = shutil.disk_usage(self.output_dir).free
            if free_space < len(combined_pcm_data) * 2:
                raise OSError("纾佺洏绌洪棿涓嶈冻")

            if self.requires_file() and self.prefers_temp_file():
                temp_path = self.build_temp_file(combined_pcm_data)

            if (hasattr(self, "delete_audio_file") and not self.delete_audio_file) or (
                self.requires_file() and not self.prefers_temp_file()
            ):
                file_path = self.save_audio_to_file(pcm_data, session_id)

            if len(combined_pcm_data) == 0:
                artifacts = None
            else:
                artifacts = ASRProviderBase.AudioArtifacts(
                    pcm_frames=pcm_data,
                    pcm_bytes=combined_pcm_data,
                    file_path=file_path,
                    temp_path=temp_path,
                )

            text, _ = await self.speech_to_text(
                opus_data, session_id, audio_format, artifacts
            )
            return text, file_path
        except OSError as e:
            logger.bind(tag=TAG).error(f"鏂囦欢鎿嶄綔閿欒: {e}")
            return None, None
        except Exception as e:
            logger.bind(tag=TAG).error(f"璇煶璇嗗埆澶辫触: {e}")
            return None, None
        finally:
            try:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
                if (
                    hasattr(self, "delete_audio_file")
                    and self.delete_audio_file
                    and file_path
                    and os.path.exists(file_path)
                ):
                    os.remove(file_path)
            except Exception as e:
                logger.bind(tag=TAG).error(f"鏂囦欢娓呯悊澶辫触: {e}")

    @abstractmethod
    async def speech_to_text(
        self,
        opus_data: List[bytes],
        session_id: str,
        audio_format="opus",
        artifacts: Optional[AudioArtifacts] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """灏嗚闊虫暟鎹浆鎹负鏂囨湰

        :param opus_data: 杈撳叆鐨凮pus闊抽鏁版嵁
        :param session_id: 浼氳瘽ID
        :param audio_format: 闊抽鏍煎紡锛岄粯璁?opus"
        :param artifacts: 闊抽宸ヤ欢锛屽寘鍚玃CM鏁版嵁銆佹枃浠惰矾寰勭瓑
        :return: 璇嗗埆缁撴灉鏂囨湰鍜屾枃浠惰矾寰勶紙濡傛灉鏈夛級
        """
        pass

    @staticmethod
    def decode_opus(opus_data: List[bytes]) -> List[bytes]:
        """灏哋pus闊抽鏁版嵁瑙ｇ爜涓篜CM鏁版嵁"""
        decoder = None
        try:
            decoder = opuslib_next.Decoder(16000, 1)
            pcm_data = []
            buffer_size = 960  # 姣忔澶勭悊960涓噰鏍风偣 (60ms at 16kHz)

            for i, opus_packet in enumerate(opus_data):
                try:
                    if not opus_packet or len(opus_packet) == 0:
                        continue

                    pcm_frame = decoder.decode(opus_packet, buffer_size)
                    if pcm_frame and len(pcm_frame) > 0:
                        pcm_data.append(pcm_frame)

                except opuslib_next.OpusError as e:
                    logger.bind(tag=TAG).warning(f"Opus瑙ｇ爜閿欒锛岃烦杩囨暟鎹寘 {i}: {e}")
                except Exception as e:
                    logger.bind(tag=TAG).error(f"闊抽澶勭悊閿欒锛屾暟鎹寘 {i}: {e}")

            return pcm_data

        except Exception as e:
            logger.bind(tag=TAG).error(f"闊抽瑙ｇ爜杩囩▼鍙戠敓閿欒: {e}")
            return []
        finally:
            if decoder is not None:
                try:
                    del decoder
                except Exception as e:
                    logger.bind(tag=TAG).debug(f"閲婃斁decoder璧勬簮鏃跺嚭閿? {e}")
