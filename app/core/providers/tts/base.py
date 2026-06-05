import os
import re
import uuid
import queue
import asyncio
import threading
import traceback
import concurrent.futures

from datetime import datetime
from core.utils import textUtils
from typing import Callable, Any
from abc import ABC, abstractmethod
from config.logger import setup_logging
from core.utils.tts import MarkdownCleaner, convert_percentage_to_range
from core.providers.tts.dto.dto import (
    TTSMessageDTO,
    SentenceType,
    ContentType,
    InterfaceType,
)

TAG = __name__
logger = setup_logging()


class TTSProviderBase(ABC):
    def __init__(self, config, delete_audio_file):
        self.interface_type = InterfaceType.NON_STREAM
        self.conn = None
        self.delete_audio_file = delete_audio_file
        self.audio_file_type = "wav"
        self.output_file = config.get("output_dir", "tmp/")
        self.tts_timeout = int(config.get("tts_timeout", 15))
        self.tts_text_queue = queue.Queue()
        self.tts_audio_queue = queue.Queue()
        self.tts_audio_first_sentence = True
        self.before_stop_play_files = []
        self.report_on_last = False
        # sentence_id 鍒版枃鏈殑鏄犲皠锛岀敤浜庢祦寮廡TS鑾峰彇姝ｇ‘鐨勫瓧骞曟枃鏈?
        self._sentence_text_map = {}
        # 鍔犺浇鏇挎崲璇嶏紝鐢ㄤ簬涓€娆℃€ф鍒欐浛鎹?
        raw_words = config.get("correct_words", [])
        self.correct_words = {}
        for item in raw_words:
            parts = item.split("|", 1)
            if len(parts) == 2:
                self.correct_words[parts[0]] = parts[1]
        # 鏋勫缓姝ｅ垯琛ㄨ揪寮忥紝浣跨敤鏈€闀垮尮閰嶄紭鍏堬紙鎺掑簭鍚庤浆涔夋嫾鎺ワ級
        if self.correct_words:
            # 鎸塳ey闀垮害闄嶅簭鎺掑垪锛岄暱鐨勫厛鍖归厤锛岄伩鍏嶇煭璇嶉儴鍒嗗共鎵?
            sorted_keys = sorted(self.correct_words.keys(), key=len, reverse=True)
            pattern_str = "|".join(re.escape(k) for k in sorted_keys)
            self._correct_words_pattern = re.compile(pattern_str)
            # 鏋勫缓鍙嶅悜鏇挎崲姝ｅ垯锛岀敤浜庡皢TTS鏈嶅姟杩斿洖鐨勬浛鎹㈠悗鏂囨湰杩樺師涓哄師濮嬫枃鏈紙瀛楀箷鏄剧ず锛?
            reverse_map = {v: k for k, v in self.correct_words.items()}
            sorted_reverse_keys = sorted(reverse_map.keys(), key=len, reverse=True)
            reverse_pattern_str = "|".join(re.escape(k) for k in sorted_reverse_keys)
            self._reverse_words_pattern = re.compile(reverse_pattern_str)
            self._reverse_words_map = reverse_map
            # 娴佸紡婊戝姩绐楀彛锛氭寜棣栧瓧鍒嗙粍鐨勬浛鎹㈣瘝瀛楀吀锛岀敤浜庡揩閫熸煡鎵?
            self._words_by_first_char = {}
            for key in sorted_keys:  # 浣跨敤宸叉寜闀垮害闄嶅簭鎺掑垪鐨刱eys锛岀‘淇濋暱璇嶄紭鍏堝尮閰?
                first_char = key[0] if key else ""
                if first_char not in self._words_by_first_char:
                    self._words_by_first_char[first_char] = []
                self._words_by_first_char[first_char].append(key)
        else:
            self._correct_words_pattern = None
            self._reverse_words_pattern = None
            self._reverse_words_map = None

        # 娴佸紡婊戝姩绐楀彛锛氬緟鍖归厤鐨勭紦瀛樻枃鏈?
        self._pending_prefix = ""
        self.tts_text_buff = []
        self.punctuations = (
            "銆?,
            "锛?,
            "?",
            "锛?,
            "!",
            "锛?,
            ";",
            "锛?,
        )
        self.first_sentence_punctuations = (
            "锛?,
            "~",
            "銆?,
            ",",
            "銆?,
            "锛?,
            "?",
            "锛?,
            "!",
            "锛?,
            ";",
            "锛?,
        )
        self.tts_stop_request = False
        self.processed_chars = 0
        self.is_first_sentence = True

    def generate_filename(self, extension=".wav"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    def handle_opus(self, opus_data: bytes):
        logger.bind(tag=TAG).debug(f"鎺ㄩ€佹暟鎹埌闃熷垪閲岄潰甯ф暟锝烇綖 {len(opus_data)}")
        self.tts_audio_queue.put((SentenceType.MIDDLE, opus_data, None, getattr(self, 'current_sentence_id', None)))

    def handle_audio_file(self, file_audio: bytes, text):
        self.before_stop_play_files.append((file_audio, text))

    def to_tts_stream(self, text, opus_handler: Callable[[bytes], None] = None) -> None:
        # 淇濈暀鍘熷鏂囨湰鐢ㄤ簬鏄剧ず/涓婃姤
        original_text = text
        text = MarkdownCleaner.clean_markdown(text)
        # 浣跨敤姝ｅ垯涓€娆℃€ф浛鎹紝閬垮厤閲嶅閬嶅巻鍜岄儴鍒嗗尮閰嶉棶棰?
        if self._correct_words_pattern:
            text = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], text)
        max_repeat_time = 5
        if self.delete_audio_file:
            # 闇€瑕佸垹闄ゆ枃浠剁殑鐩存帴杞负闊抽鏁版嵁
            while max_repeat_time > 0:
                try:
                    audio_bytes = asyncio.run(self.text_to_speak(text, None))
                    if audio_bytes:
                        # 浣跨敤鍘熷鏂囨湰鐢ㄤ簬鏄剧ず/涓婃姤
                        self.tts_audio_queue.put((SentenceType.FIRST, None, original_text, getattr(self, 'current_sentence_id', None)))
                        audio_bytes_to_data_stream(
                            audio_bytes,
                            file_type=self.audio_file_type,
                            is_opus=True,
                            callback=opus_handler,
                            sample_rate=self.conn.sample_rate,
                            opus_encoder=self.opus_encoder,
                        )
                        break
                    else:
                        max_repeat_time -= 1
                except Exception as e:
                    logger.bind(tag=TAG).warning(
                        f"璇煶鐢熸垚澶辫触{5 - max_repeat_time + 1}娆? {original_text}锛岄敊璇? {e}"
                    )
                    max_repeat_time -= 1
            if max_repeat_time > 0:
                logger.bind(tag=TAG).info(
                    f"璇煶鐢熸垚鎴愬姛: {original_text}锛岄噸璇晎5 - max_repeat_time}娆?
                )
            else:
                logger.bind(tag=TAG).error(
                    f"璇煶鐢熸垚澶辫触: {original_text}锛岃妫€鏌ョ綉缁滄垨鏈嶅姟鏄惁姝ｅ父"
                )
            return None
        else:
            tmp_file = self.generate_filename()
            try:
                while not os.path.exists(tmp_file) and max_repeat_time > 0:
                    try:
                        asyncio.run(self.text_to_speak(text, tmp_file))
                    except Exception as e:
                        logger.bind(tag=TAG).warning(
                            f"璇煶鐢熸垚澶辫触{5 - max_repeat_time + 1}娆? {original_text}锛岄敊璇? {e}"
                        )
                        # 鏈墽琛屾垚鍔燂紝鍒犻櫎鏂囦欢
                        if os.path.exists(tmp_file):
                            os.remove(tmp_file)
                        max_repeat_time -= 1

                if max_repeat_time > 0:
                    logger.bind(tag=TAG).info(
                        f"璇煶鐢熸垚鎴愬姛: {original_text}:{tmp_file}锛岄噸璇晎5 - max_repeat_time}娆?
                    )
                else:
                    logger.bind(tag=TAG).error(
                        f"璇煶鐢熸垚澶辫触: {original_text}锛岃妫€鏌ョ綉缁滄垨鏈嶅姟鏄惁姝ｅ父"
                    )
                self.tts_audio_queue.put((SentenceType.FIRST, None, original_text, getattr(self, 'current_sentence_id', None)))
                self._process_audio_file_stream(tmp_file, callback=opus_handler)
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to generate TTS file: {e}")
                return None
    
    def to_tts(self, text):
        # 淇濈暀鍘熷鏂囨湰鐢ㄤ簬鏃ュ織/鏄剧ず
        original_text = text
        text = MarkdownCleaner.clean_markdown(text)
        if self._correct_words_pattern:
            text = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], text)
        max_repeat_time = 5
        if self.delete_audio_file:
            # 闇€瑕佸垹闄ゆ枃浠剁殑鐩存帴杞负闊抽鏁版嵁
            while max_repeat_time > 0:
                try:
                    audio_bytes = asyncio.run(self.text_to_speak(text, None))
                    if audio_bytes:
                        audio_datas = []
                        audio_bytes_to_data_stream(
                            audio_bytes,
                            file_type=self.audio_file_type,
                            is_opus=True,
                            callback=lambda data: audio_datas.append(data),
                            sample_rate=self.conn.sample_rate,
                        )
                        return audio_datas
                    else:
                        max_repeat_time -= 1
                except Exception as e:
                    logger.bind(tag=TAG).warning(
                        f"璇煶鐢熸垚澶辫触{5 - max_repeat_time + 1}娆? {original_text}锛岄敊璇? {e}"
                    )
                    max_repeat_time -= 1
            if max_repeat_time > 0:
                logger.bind(tag=TAG).info(
                    f"璇煶鐢熸垚鎴愬姛: {original_text}锛岄噸璇晎5 - max_repeat_time}娆?
                )
            else:
                logger.bind(tag=TAG).error(
                    f"璇煶鐢熸垚澶辫触: {original_text}锛岃妫€鏌ョ綉缁滄垨鏈嶅姟鏄惁姝ｅ父"
                )
            return None
        else:
            tmp_file = self.generate_filename()
            try:
                while not os.path.exists(tmp_file) and max_repeat_time > 0:
                    try:
                        asyncio.run(self.text_to_speak(text, tmp_file))
                    except Exception as e:
                        logger.bind(tag=TAG).warning(
                            f"璇煶鐢熸垚澶辫触{5 - max_repeat_time + 1}娆? {original_text}锛岄敊璇? {e}"
                        )
                        # 鏈墽琛屾垚鍔燂紝鍒犻櫎鏂囦欢
                        if os.path.exists(tmp_file):
                            os.remove(tmp_file)
                        max_repeat_time -= 1

                if max_repeat_time > 0:
                    logger.bind(tag=TAG).info(
                        f"璇煶鐢熸垚鎴愬姛: {original_text}:{tmp_file}锛岄噸璇晎5 - max_repeat_time}娆?
                    )
                else:
                    logger.bind(tag=TAG).error(
                        f"璇煶鐢熸垚澶辫触: {original_text}锛岃妫€鏌ョ綉缁滄垨鏈嶅姟鏄惁姝ｅ父"
                    )

                return tmp_file
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to generate TTS file: {e}")
                return None

    @abstractmethod
    async def text_to_speak(self, text, output_file):
        pass

    def audio_to_pcm_data_stream(
        self, audio_file_path, callback: Callable[[Any], Any] = None
    ):
        """闊抽鏂囦欢杞崲涓篜CM缂栫爜"""
        return audio_to_data_stream(audio_file_path, is_opus=False, callback=callback, sample_rate=self.conn.sample_rate, opus_encoder=None)

    def audio_to_opus_data_stream(
        self, audio_file_path, callback: Callable[[Any], Any] = None
    ):
        """闊抽鏂囦欢杞崲涓篛pus缂栫爜"""
        return audio_to_data_stream(audio_file_path, is_opus=True, callback=callback, sample_rate=self.conn.sample_rate, opus_encoder=self.opus_encoder)

    def tts_one_sentence(
        self,
        conn,
        content_type,
        content_detail=None,
        content_file=None,
        sentence_id=None,
    ):
        """鍙戦€佷竴鍙ヨ瘽"""
        if not sentence_id:
            if conn.sentence_id:
                sentence_id = conn.sentence_id
            else:
                sentence_id = str(uuid.uuid4().hex)
                conn.sentence_id = sentence_id
        # 瀵逛簬鍗曞彞鐨勬枃鏈紝杩涜鍒嗘澶勭悊
        segments = re.split(r"([銆傦紒锛??锛?\n])", content_detail)
        for seg in segments:
            self.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=sentence_id,
                    sentence_type=SentenceType.MIDDLE,
                    content_type=content_type,
                    content_detail=seg,
                    content_file=content_file,
                )
            )

    async def open_audio_channels(self, conn):
        self.conn = conn

        # 鏍规嵁conn鐨剆ample_rate鍒涘缓缂栫爜鍣紝濡傛灉瀛愮被宸茬粡鍒涘缓鍒欎笉瑕嗙洊锛圛ndexTTS鎺ュ彛杩斿洖涓?4kHZ-寰呴噸閲囨牱澶勭悊锛?
        if not hasattr(self, 'opus_encoder') or self.opus_encoder is None:
            self.opus_encoder = opus_encoder_utils.OpusEncoderUtils(
                sample_rate=conn.sample_rate, channels=1, frame_size_ms=60
            )

        # tts 娑堝寲绾跨▼
        self.tts_priority_thread = threading.Thread(
            target=self.tts_text_priority_thread, daemon=True
        )
        self.tts_priority_thread.start()

        # 闊抽鎾斁 娑堝寲绾跨▼
        self.audio_play_priority_thread = threading.Thread(
            target=self._audio_play_priority_thread, daemon=True
        )
        self.audio_play_priority_thread.start()

    def store_tts_text(self, sentence_id, text):
        """瀛樺偍鎸囧畾 sentence_id 瀵瑰簲鐨勬枃鏈紝鐢ㄤ簬娴佸紡TTS鑾峰彇姝ｇ‘鐨勫瓧骞曟枃鏈?

        Args:
            sentence_id: 浼氳瘽ID
            text: 瑕佸瓨鍌ㄧ殑鏂囨湰
        """
        if sentence_id and text:
            self._sentence_text_map[sentence_id] = text
            # 鍙繚鐣欐渶杩?5 涓紝闃叉鍐呭瓨娉勬紡
            if len(self._sentence_text_map) > 5:
                oldest = next(iter(self._sentence_text_map))
                del self._sentence_text_map[oldest]

    def get_tts_text(self, sentence_id):
        """鑾峰彇鎸囧畾 sentence_id 瀵瑰簲鐨勬枃鏈?

        Args:
            sentence_id: 浼氳瘽ID

        Returns:
            str: 瀵瑰簲鐨勬枃鏈紝濡傛灉涓嶅瓨鍦ㄨ繑鍥?None
        """
        return self._sentence_text_map.get(sentence_id)

    def clear_tts_text(self, sentence_id):
        """娓呴櫎鎸囧畾 sentence_id 鐨勬枃鏈?

        Args:
            sentence_id: 浼氳瘽ID
        """
        if sentence_id in self._sentence_text_map:
            del self._sentence_text_map[sentence_id]

    def _restore_original_text(self, text):
        if not self._reverse_words_pattern or not text:
            return text
        return self._reverse_words_pattern.sub(
            lambda m: self._reverse_words_map[m.group(0)], text
        )

    # 杩欓噷榛樿鏄潪娴佸紡鐨勫鐞嗘柟寮?
    # 娴佸紡澶勭悊鏂瑰紡璇峰湪瀛愮被涓噸鍐?
    def tts_text_priority_thread(self):
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)
                if self.conn.client_abort:
                    logger.bind(tag=TAG).info("鏀跺埌鎵撴柇淇℃伅锛岀粓姝TS鏂囨湰澶勭悊绾跨▼")
                    continue
                # 杩囨护鏃ф秷鎭細妫€鏌entence_id鏄惁鍖归厤
                if message.sentence_id != self.conn.sentence_id:
                    continue
                if message.sentence_type == SentenceType.FIRST:
                    self.current_sentence_id = message.sentence_id
                    self.tts_stop_request = False
                    self.processed_chars = 0
                    self.tts_text_buff = []
                    self.is_first_sentence = True
                    self.tts_audio_first_sentence = True
                elif ContentType.TEXT == message.content_type:
                    self.tts_text_buff.append(message.content_detail)
                    segment_text = self._get_segment_text()
                    if segment_text:
                        self.to_tts_stream(segment_text, opus_handler=self.handle_opus)
                elif ContentType.FILE == message.content_type:
                    self._process_remaining_text_stream(opus_handler=self.handle_opus)
                    tts_file = message.content_file
                    if tts_file and os.path.exists(tts_file):
                        self._process_audio_file_stream(
                            tts_file, callback=self.handle_opus
                        )
                if message.sentence_type == SentenceType.LAST:
                    self._process_remaining_text_stream(opus_handler=self.handle_opus)
                    self.tts_audio_queue.put(
                        (message.sentence_type, [], message.content_detail, message.sentence_id)
                    )

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"澶勭悊TTS鏂囨湰澶辫触: {str(e)}, 绫诲瀷: {type(e).__name__}, 鍫嗘爤: {traceback.format_exc()}"
                )
                continue

    def _audio_play_priority_thread(self):
        # 闇€瑕佷笂鎶ョ殑鏂囨湰鍜岄煶棰戝垪琛?
        enqueue_text = None
        enqueue_audio = []
        while not self.conn.stop_event.is_set():
            text = None
            try:
                try:
                    item = self.tts_audio_queue.get(timeout=0.1)
                    if len(item) == 4:
                        sentence_type, audio_datas, text, sentence_id = item
                    else:
                        sentence_type, audio_datas, text = item
                        sentence_id = None
                except queue.Empty:
                    if self.conn.stop_event.is_set():
                        break
                    continue

                if self.conn.client_abort:
                    logger.bind(tag=TAG).debug("鏀跺埌鎵撴柇淇″彿锛岃烦杩囧綋鍓嶉煶棰戞暟鎹?)
                    enqueue_text, enqueue_audio = None, []
                    continue

                # 鏀跺埌涓嬩竴涓枃鏈紑濮嬫垨浼氳瘽缁撴潫鏃惰繘琛屼笂鎶?
                if sentence_type is not SentenceType.MIDDLE:
                    if self.report_on_last:
                        # 绱Н妯″紡锛氶€傜敤浜庡叏绋嬪彧鏈変竴涓闊虫祦鐨凾TS锛堝seed-tts-2.0锛?
                        # FIRST鏃跺彧璁板綍鏂囨湰锛岄煶棰戞寔缁疮绉紝浠呭湪LAST鏃剁粺涓€涓婃姤
                        if text:
                            enqueue_text = text
                        if sentence_type == SentenceType.LAST:
                            enqueue_tts_report(self.conn, enqueue_text, enqueue_audio)
                            enqueue_audio = []
                            enqueue_text = None
                    else:
                        # 闈炵疮绉ā寮忥細姣忎釜鍙ュ瓙鍒嗗埆涓婃姤
                        if enqueue_text is not None:
                            enqueue_tts_report(self.conn, enqueue_text, enqueue_audio)
                        enqueue_audio = []
                        enqueue_text = text

                # 鏀堕泦涓婃姤闊抽鏁版嵁
                if isinstance(audio_datas, bytes):
                    enqueue_audio.append(audio_datas)

                # 鍙戦€侀煶棰?
                future = asyncio.run_coroutine_threadsafe(
                    sendAudioMessage(self.conn, sentence_type, audio_datas, text, sentence_id),
                    self.conn.loop,
                )
                future.result()

                # 璁板綍杈撳嚭鍜屾姤鍛?
                if self.conn.max_output_size > 0 and text:
                    add_device_output(self.conn.headers.get("device-id"), len(text))

            except Exception as e:
                logger.bind(tag=TAG).error(f"audio_play_priority_thread: {text} {e}")

    async def start_session(self, session_id):
        pass

    async def finish_session(self, session_id):
        pass

    async def close(self):
        """璧勬簮娓呯悊鏂规硶"""
        self._sentence_text_map.clear()
        if hasattr(self, "ws") and self.ws:
            await self.ws.close()

    def _get_segment_text(self):
        # 鍚堝苟褰撳墠鍏ㄩ儴鏂囨湰骞跺鐞嗘湭鍒嗗壊閮ㄥ垎
        full_text = "".join(self.tts_text_buff)
        current_text = full_text[self.processed_chars :]  # 浠庢湭澶勭悊鐨勪綅缃紑濮?
        last_punct_pos = -1

        # 鏍规嵁鏄惁鏄涓€鍙ヨ瘽閫夋嫨涓嶅悓鐨勬爣鐐圭鍙烽泦鍚?
        punctuations_to_use = (
            self.first_sentence_punctuations
            if self.is_first_sentence
            else self.punctuations
        )

        for punct in punctuations_to_use:
            pos = current_text.rfind(punct)
            if (pos != -1 and last_punct_pos == -1) or (
                pos != -1 and pos < last_punct_pos
            ):
                last_punct_pos = pos

        if last_punct_pos != -1:
            segment_text_raw = current_text[: last_punct_pos + 1]
            segment_text = textUtils.get_string_no_punctuation_or_emoji(
                segment_text_raw
            )
            self.processed_chars += len(segment_text_raw)  # 鏇存柊宸插鐞嗗瓧绗︿綅缃?

            # 濡傛灉鏄涓€鍙ヨ瘽锛屽湪鎵惧埌绗竴涓€楀彿鍚庯紝灏嗘爣蹇楄缃负False
            if self.is_first_sentence:
                self.is_first_sentence = False

            return segment_text
        elif self.tts_stop_request and current_text:
            segment_text = current_text
            self.is_first_sentence = True  # 閲嶇疆鏍囧織
            return segment_text
        else:
            return None

    def _process_audio_file_stream(
        self, tts_file, callback: Callable[[Any], Any]
    ) -> None:
        """澶勭悊闊抽鏂囦欢骞惰浆鎹负鎸囧畾鏍煎紡

        Args:
            tts_file: 闊抽鏂囦欢璺緞
            callback: 鏂囦欢澶勭悊鍑芥暟
        """
        if tts_file.endswith(".p3"):
            p3.decode_opus_from_file_stream(tts_file, callback=callback)
        elif self.conn.audio_format == "pcm":
            self.audio_to_pcm_data_stream(tts_file, callback=callback)
        else:
            self.audio_to_opus_data_stream(tts_file, callback=callback)

        if (
            self.delete_audio_file
            and tts_file is not None
            and os.path.exists(tts_file)
            and tts_file.startswith(self.output_file)
        ):
            os.remove(tts_file)

    def _process_before_stop_play_files(self):
        for audio_datas, text in self.before_stop_play_files:
            self.tts_audio_queue.put((SentenceType.MIDDLE, audio_datas, text, getattr(self, 'current_sentence_id', None)))
        self.before_stop_play_files.clear()
        self.tts_audio_queue.put((SentenceType.LAST, [], None, getattr(self, 'current_sentence_id', None)))

    def _process_remaining_text_stream(
        self, opus_handler: Callable[[bytes], None] = None
    ):
        """澶勭悊鍓╀綑鐨勬枃鏈苟鐢熸垚璇煶

        Returns:
            bool: 鏄惁鎴愬姛澶勭悊浜嗘枃鏈?
        """
        full_text = "".join(self.tts_text_buff)
        remaining_text = full_text[self.processed_chars :]
        if remaining_text:
            segment_text = textUtils.get_string_no_punctuation_or_emoji(remaining_text)
            if segment_text:
                self.to_tts_stream(segment_text, opus_handler=opus_handler)
                self.processed_chars += len(full_text)
                return True
        return False

    def _apply_percentage_params(self, config):
        """鏍规嵁瀛愮被瀹氫箟鐨?TTS_PARAM_CONFIG 鎵归噺搴旂敤鐧惧垎姣斿弬鏁?""
        for config_key, attr_name, min_val, max_val, base_val, transform in self.TTS_PARAM_CONFIG:
            if config_key in config:
                val = convert_percentage_to_range(config[config_key], min_val, max_val, base_val)
                setattr(self, attr_name, transform(val) if transform else val)

    def _match_stream_text(self, text):
        """娴佸紡鏂囨湰婊戝姩绐楀彛鍖归厤锛岀敤浜庡鐞嗚法鍒嗙墖鐨勬浛鎹㈣瘝

        Args:
            text: 杈撳叆鐨勬枃鏈墖娈?

        Returns:
            tuple: (纭畾鐨勬枃鏈垪琛? 鍓╀綑寰呭尮閰嶇殑鍓嶇紑)
        """
        if not self.correct_words or not text:
            return [text] if text else [], ""

        result = []
        pending = self._pending_prefix
        i = 0

        while i < len(text):
            char = text[i]

            # 灏濊瘯锛歱ending + 褰撳墠瀛楃 鏄惁鑳藉尮閰嶆浛鎹㈣瘝
            test_text = pending + char

            matched = False
            # 閬嶅巻鍙兘鍖归厤鐨勬浛鎹㈣瘝
            candidates = self._words_by_first_char.get(pending[0], []) if pending else self._words_by_first_char.get(char, [])
            for key in candidates:
                if test_text == key:
                    # 瀹屾暣鍖归厤锛屾浛鎹㈠悗鍙戦€?
                    result.append(self.correct_words[key])
                    pending = ""
                    matched = True
                    break
                elif key.startswith(test_text):
                    # 鏄浛鎹㈣瘝鐨勫墠缂€锛岀户缁瓑寰?
                    pending = test_text
                    matched = True
                    break

            if matched:
                i += 1
                continue

            # 娌℃湁鍖归厤鍒版洿闀跨殑璇嶏紝pending 鐨勫唴瀹圭‘瀹氬彲浠ュ彂閫?
            if pending:
                result.append(pending)
                pending = ""

            # 妫€鏌ュ綋鍓嶅瓧绗︽槸鍚︽槸鏌愪釜鏇挎崲璇嶇殑寮€澶?
            if char in self._words_by_first_char:
                pending = char
            else:
                result.append(char)

            i += 1

        return result, pending

    def reset_stream_state(self):
        """閲嶇疆娴佸紡澶勭悊鐘舵€侊紝鐢ㄤ簬浼氳瘽寮€濮嬫椂娓呯悊娈嬬暀鐘舵€?""
        self._pending_prefix = ""


    async def synthesize(self, text: str):
        """一站式合成：文本 → 音频字节（供 REST API 使用）"""
        output_file = self.generate_filename()
        await self.text_to_speak(text, output_file)
        with open(output_file, "rb") as f:
            data = f.read()
        if self.delete_audio_file and os.path.exists(output_file):
            os.remove(output_file)
        return data
