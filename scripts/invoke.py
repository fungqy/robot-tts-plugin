
import base64
import json
import os
import re
import struct
import sys
import unicodedata
import uuid 
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.tts.v20190823 import tts_client, models

from models import TtsRequest, TtsResponse, Subtitle


# ============================================================
# 1. 长文本切割
# ============================================================

class TextSplitter:
    """智能文本切割，优先在句子边界断开，避免切断小数"""

    # 匹配句子结束符：前面不能是数字的 . 。！!？?
    SENTENCE_ENDINGS = re.compile(r'(?<!\d)[.。！!？?]')

    @classmethod
    def split(cls, text: str, max_len: int = 100) -> list[str]:
        if not text or len(text) <= max_len:
            return [text] if text else []

        segments = []
        start = 0

        while start < len(text):
            # 1. 优先在 max_len 以内寻找最后一个句号
            search_end = min(start + max_len, len(text))
            search_range = text[start:search_end]
            cut_pos = cls._find_best_sentence_break(search_range)

            # 2. max_len 内没找到，再在 [max_len, max_len+20) 内找第一个句号
            #    （仅扩大 20 字符，避免切出过长分段）
            if cut_pos == -1 and search_end < len(text):
                extra_end = min(start + max_len + 20, len(text))
                extra_range = text[start:extra_end]
                # 在扩展范围内找第一个可用的句号
                for match in cls.SENTENCE_ENDINGS.finditer(extra_range):
                    pos = match.end()
                    word_start = max(0, pos - 20)
                    potential_word = extra_range[word_start:pos]
                    last_token = cls._extract_last_token(potential_word)
                    if not re.search(r'\d', last_token):
                        cut_pos = pos
                        break

            if cut_pos != -1:
                # 在句号处切割
                abs_cut = start + cut_pos
                segments.append(text[start:abs_cut].strip())
                start = abs_cut
            else:
                # 3. 没找到句号，尝试按空格切割
                space_pos = cls._find_safe_space_break(search_range, max_len)
                if space_pos != -1:
                    abs_cut = start + space_pos
                else:
                    # 4. 兜底：强制截断
                    abs_cut = start + max_len
                    if abs_cut > len(text):
                        abs_cut = len(text)

                segments.append(text[start:abs_cut].strip())
                start = abs_cut

            # 去除前导空格
            while start < len(text) and text[start] == ' ':
                start += 1

        # 去除只包含标点/空白的分段（\p{P} 在 Python re 不支持，改用 Unicode 判断）
        segments = [s for s in segments if s and not all(
            unicodedata.category(c).startswith('P') or c.isspace() for c in s
        )]
        return segments

    @classmethod
    def _find_best_sentence_break(cls, text: str) -> int:
        """寻找最佳的句子结束位置（句号位置）"""
        last_valid_pos = -1
        for match in cls.SENTENCE_ENDINGS.finditer(text):
            pos = match.end()  # 句号后的位置
            # 检查句号前是否包含数字（避免错误切割小数）
            word_start = max(0, pos - 20)
            potential_word = text[word_start:pos]
            last_token = cls._extract_last_token(potential_word)
            if not re.search(r'\d', last_token):
                last_valid_pos = pos
        return last_valid_pos

    @classmethod
    def _find_safe_space_break(cls, text: str, max_len: int) -> int:
        """从 max_len 位置向前寻找最后一个空格"""
        limit = min(max_len, len(text))
        for i in range(limit - 1, -1, -1):
            if text[i] == ' ':
                return i
        return -1

    @classmethod
    def _extract_last_token(cls, s: str) -> str:
        """提取字符串中最后一个连续的字母数字序列（含小数点）"""
        s = s.strip()
        if not s:
            return ""
        end = len(s)
        start = end
        while start > 0:
            c = s[start - 1]
            if c.isalnum() or c == '.':
                start -= 1
            else:
                break
        return s[start:end]

# ============================================================
# 3. 单段 TTS 合成
# ============================================================

class TencentTtsClient:
    """腾讯云 TTS 客户端封装"""

    def __init__(self, secret_id: str, secret_key: str, region: str = "ap-guangzhou"):
        cred = credential.Credential(secret_id, secret_key)
        self.client = tts_client.TtsClient(cred, region)

    def text_to_voice(self, param: models.TextToVoiceRequest) -> TtsResponse:
        """单段文字转语音"""


        try:
            resp = self.client.TextToVoice(param)

            result = TtsResponse(
                audio_base64=resp.Audio or "",
                request_id=resp.RequestId or "",
            )

            # 解析字幕
            if param.EnableSubtitle and hasattr(resp, 'Subtitles'):
                for sub in (resp.Subtitles or []):
                    result.subtitles.append(Subtitle(
                        Text=sub.Text,
                        BeginIndex=sub.BeginIndex,
                        EndIndex=sub.EndIndex,
                        BeginTime=sub.BeginTime,
                        EndTime=sub.EndTime,
                    ))

            return result

        except TencentCloudSDKException as e:
            raise Exception(e)

# ============================================================
# 4. 音频合并 (对应 Java MemoryAudioMerger)
# ============================================================

class AudioMerger:
    """音频合并器"""

    WAV_HEADER_SIZE = 44

    @classmethod
    def concat_audio(cls, results: list[TtsResponse], codec: str = "wav") -> bytes:
        """合并多段 TTS 音频"""
        if not results:
            raise ValueError("结果列表为空")

        if codec == "wav":
            return cls._concat_wav(results)
        elif codec == "mp3":
            return cls._concat_mp3(results)
        else:
            # pcm 或其他格式直接拼接
            return cls._concat_raw(results)

    @classmethod
    def _concat_wav(cls, results: list[TtsResponse]) -> bytes:
        """合并 WAV 格式音频：跳过 WAV 头，拼接 PCM 数据，生成新 WAV 头"""
        total_pcm_size = 0
        pcm_chunks = []

        for result in results:
            wav_bytes = base64.b64decode(result.audio_base64)
            if len(wav_bytes) <= cls.WAV_HEADER_SIZE:
                continue
            # 跳过 WAV 文件头，取 PCM 数据
            pcm_data = wav_bytes[cls.WAV_HEADER_SIZE:]
            pcm_chunks.append(pcm_data)
            total_pcm_size += len(pcm_data)

        if not pcm_chunks:
            raise RuntimeError("没有有效的音频数据可合并")

        # 合并 PCM 数据
        merged_pcm = b''.join(pcm_chunks)

        # 创建 WAV 文件头 (16kHz, 16bit, 单声道)
        wav_header = cls._create_wav_header(total_pcm_size, 16000, 16, 1)
        return wav_header + merged_pcm

    @classmethod
    def _create_wav_header(cls, data_size: int, sample_rate: int,
                           bits_per_sample: int, channels: int) -> bytes:
        """创建 WAV 文件头"""
        header = bytearray(44)

        # RIFF 头
        header[0:4] = b'RIFF'
        struct.pack_into('<I', header, 4, 36 + data_size)   # 文件大小
        header[8:12] = b'WAVE'

        # fmt 子块
        header[12:16] = b'fmt '
        struct.pack_into('<I', header, 16, 16)              # fmt 块大小
        struct.pack_into('<H', header, 20, 1)               # PCM 格式
        struct.pack_into('<H', header, 22, channels)        # 声道数
        struct.pack_into('<I', header, 24, sample_rate)     # 采样率
        byte_rate = sample_rate * channels * bits_per_sample // 8
        struct.pack_into('<I', header, 28, byte_rate)       # 字节率
        block_align = channels * bits_per_sample // 8
        struct.pack_into('<H', header, 32, block_align)     # 块对齐
        struct.pack_into('<H', header, 34, bits_per_sample) # 位深

        # data 子块
        header[36:40] = b'data'
        struct.pack_into('<I', header, 40, data_size)       # 数据大小

        return bytes(header)

    @classmethod
    def _concat_mp3(cls, results: list[TtsResponse]) -> bytes:
        """MP3 格式直接拼接"""
        chunks = []
        for result in results:
            audio_bytes = base64.b64decode(result.audio_base64)
            chunks.append(audio_bytes)
        return b''.join(chunks)

    @classmethod
    def _concat_raw(cls, results: list[TtsResponse]) -> bytes:
        """PCM 等裸格式直接拼接"""
        chunks = []
        for result in results:
            audio_bytes = base64.b64decode(result.audio_base64)
            chunks.append(audio_bytes)
        return b''.join(chunks)

# ============================================================
# 5. 字幕时间戳累加
# ============================================================

def accumulate_subtitles(segments: list[TtsResponse]) -> list[Subtitle]:
    """
    累加各段字幕的时间戳和索引。
    每段合成结果中的字幕 begin/end 都是从 0 开始，
    需要累加前一段的结束值。
    """
    all_subtitles: list[Subtitle] = []
    last_end_index = 0
    last_end_time = 0

    for segment in segments:
        if not segment.subtitles:
            continue

        for sub in segment.subtitles:
            # 偏移累加
            sub.BeginIndex += last_end_index
            sub.EndIndex += last_end_index
            sub.BeginTime += last_end_time
            sub.EndTime += last_end_time
            all_subtitles.append(sub)

        # 更新偏移基准
        last_sub = segment.subtitles[-1]
        last_end_index = last_sub.EndIndex
        last_end_time = last_sub.EndTime

    return all_subtitles

# ============================================================
# 6. 完整合成流程
# ============================================================

class LongTextTtsEngine:
    """
    长文本 TTS 合成引擎
    集成：文本切割 → 分段并行合成 → 音频合并 → 字幕累加
    """

    def __init__(self, secret_id: str, secret_key: str, region: str = "ap-guangzhou",
                 max_workers: int = 10):
        self.tts_client = TencentTtsClient(secret_id, secret_key, region)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.max_chunk_len = 100  # 每段最大字符数

    def synthesize(self, param: TtsRequest) -> dict:
        """
        完整合成流程

        Returns:
            {
                "audio_bytes": b'...',    # 合并后的音频字节
                "subtitles": [...],       # 累加后的字幕列表
                "duration": int,          # 总时长(毫秒)
                "segment_count": int,     # 分段数
            }
        """
        # 1. 文本切割
        chunks = TextSplitter.split(param.text, self.max_chunk_len)
        if not chunks:
            raise ValueError("文本切割后为空")

        # 2. 分段并行合成
        future_to_chunk = {}
        for i, chunk in enumerate(chunks):
            chunk_request = param.build_tencent_request(chunk)
            future = self.executor.submit(self.tts_client.text_to_voice, chunk_request)
            future_to_chunk[future] = i

        # 收集结果
        segments: list[Optional[TtsResponse]] = [None] * len(chunks)
        first_error: Optional[BaseException] = None
        for future in as_completed(future_to_chunk):
            idx = future_to_chunk[future]
            try:
                segments[idx] = future.result()
            except Exception as e:
                # 记录第一个错误，继续收集其他已完成结果后统一抛出
                if first_error is None:
                    first_error = RuntimeError(e)

        # 若有段失败，取消尚未开始的 future 并抛出第一个错误
        if first_error is not None:
            for future in future_to_chunk:
                future.cancel()
            raise first_error

        # 此时所有结果都已回填，断言无 None
        assert all(s is not None for s in segments)
        results: list[TtsResponse] = segments  # type: ignore[assignment]

        # 3. 音频合并
        audio_bytes = AudioMerger.concat_audio(results)

        # 4. 字幕时间戳累加
        subtitles = []
        duration = 0
        if param.enable_subtitle:
            subtitles = accumulate_subtitles(results)
            if subtitles:
                duration = subtitles[-1].EndTime

        return {
            "audio_bytes": audio_bytes,
            "subtitles": subtitles,
            "duration": duration,
            "segment_count": len(chunks),
        }

    def close(self):
        self.executor.shutdown(wait=True)


def execute(parameters: dict[str, Any]):
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    # 配置
    SECRET_ID = os.getenv("TENCENT_ACCESS_SECRET_ID")
    SECRET_KEY = os.getenv("TENCENT_ACCESS_SECRET_KEY")

    if not SECRET_ID or not SECRET_KEY:
        raise ValueError("TENCENT_ACCESS_SECRET_ID 和 TENCENT_ACCESS_SECRET_KEY 不能为空")

    # 解析参数
    try:
        request = TtsRequest(**parameters)
    except ValueError as e:
        print({"success": False, "message": str(e),})
        return

    engine: Optional[LongTextTtsEngine] = None
    try:
        engine = LongTextTtsEngine(SECRET_ID, SECRET_KEY, max_workers=5)
        result = engine.synthesize(request)

        # 确保目录存在
        os.makedirs(request.save_path, exist_ok=True)

        # 生成文件的绝对路径，若save_path已包含路径分隔符，直接拼接，否则添加分隔符
        if not request.save_path.endswith(os.path.sep):
            request.save_path += os.path.sep

        audio_file = request.save_path +f"{uuid.uuid4()}.wav"
        subtitle_file = audio_file.replace(".wav", ".json")

        # 保存音频文件
        with open(audio_file, "wb") as f:
            f.write(result["audio_bytes"])

        # 保存字幕文件（dataclass 需转 dict 才能被 json 序列化）
        if request.enable_subtitle:
            with open(subtitle_file, "w", encoding="utf-8") as f:
                import json
                from dataclasses import asdict
                subtitles_data = [asdict(s) for s in result["subtitles"]]
                json.dump(subtitles_data, f, ensure_ascii=False, indent=2)

        print({
            "success": True,
            "data": {
                "audio_file": audio_file,
                "subtitle_file": subtitle_file,
                "duration": result["duration"],
            }
        })

    except Exception as e:
        print({"success": False, "message": str(e),})
    finally:
        if engine is not None:
            engine.close()


def main():
    input_data = sys.stdin.read().strip()
    if not input_data:
        input_data = "{}"
        
    request = json.loads(input_data)
    execute(request.get("parameters", {}))

if __name__ == "__main__":
    main()



