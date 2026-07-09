import os
import time
import uuid

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

from tencentcloud.tts.v20190823.models import TextToVoiceRequest


FAST_VOICE_TYPE_ID = 200000000  # 复刻音色固定 ID


@dataclass
class Subtitle:
    """字幕时间戳"""
    Text: str = ""
    BeginIndex: int = 0
    EndIndex: int = 0
    BeginTime: int = 0
    EndTime: int = 0


@dataclass
class TtsResponse:
    """TTS 合成结果"""
    audio_base64: str = ""
    subtitles: list[Subtitle] = field(default_factory=list)
    request_id: str = ""


@dataclass
class TtsRequest:
    def looks_like_dir(self) -> bool:
        """必须是绝对路径,且最后一段不含文件扩展名"""
        if not self.save_path:
            return False
        # 必须是绝对路径
        if not os.path.isabs(self.save_path):
            return False
        # 去掉末尾分隔符,取最后一段
        last = os.path.basename(self.save_path.rstrip('/\\'))
        # 最后一段不含点,说明不像文件
        return '.' not in last

    text: str = ""                  # 待合成文本
    save_path: str = ""             # 音频文件保存路径
    voice_id: str = "101008"        # 音色
    source: str = "offical"         # 音色来源: offical(官方音色) 或 personal(个人音色)
    volume: float = 0               # 音量 [-10, 10]
    speed: float = 1.0              # 语速 [-2, 6]
    primary_language: int = 1       # 主语言类型: 1 (中文) 或 2 (英文)
    sample_rate: int = 16000        # 音频采样率: 16000 或 8000
    enable_subtitle: bool = True    # 是否开启字幕
    # emotion_category: str = ""    # 音频情感,仅支持多情感音色使用。取值: neutral(中性)、sad(悲伤)、happy(高兴)、angry(生气)、fear(恐惧)、news(新闻)、story(故事)、radio(广播)、poetry(诗歌)、call(客服)、sajiao(撒娇)、disgusted(厌恶)、amaze(震惊)、peaceful(平静)、exciting(兴奋)、aojiao(傲娇)、jieshuo(解说)
    # emotion_intensity: float = 0  # 情感程度,取值范围为[50,200],默认为100；只有EmotionCategory不为空时生效。

    def __post_init__(self):
        # 1. 取值校验
        # 1.1 volume 范围 [-10, 10]
        if not -10 <= self.volume <= 10:
            raise ValueError("volume(音量) 必须在 [-10, 10] 范围内")
        # 1.2 speed 范围 [-2, 6]
        if not -2 <= self.speed <= 6:
            raise ValueError("speed(语速) 必须在 [-2, 6] 范围内")
        # 1.3 primary_language 只能为 1 或 2（1 为中文,2 为英文）
        if self.primary_language not in [1, 2]:
            raise ValueError("primary_language(主语言类型)必须为 1 (中文) 或 2 (英文)")
        # 1.4 sample_rate 只能为 16000 或 8000
        if self.sample_rate not in [16000, 8000]:
            raise ValueError("sample_rate(音频采样率)必须为 16000 或 8000")
        # 1.5 save_path 不能为空,且必须是一个目录（可以不存在,不存在时会自动创建）
        if not self.save_path or not self.looks_like_dir():
            raise ValueError("save_path(音频文件保存路径)不能为空, 且必须是一个目录")
        # 1.6 source 只能为 offical 或 personal
        if self.source not in ["offical", "personal"]:
            raise ValueError("source(音色来源)必须为 offical(官方音色) 或 personal(个人音色)")
        # 1.7 text 不能为空
        if not self.text:
            raise ValueError("text(待合成文本)不能为空")

        # 2. speed 截断到两位小数（非四舍五入）
        d = Decimal(str(self.speed)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        self.speed = float(d)
    
    def build_tencent_request(self, text: str | None) -> "TextToVoiceRequest":
        """从 TtsRequest 转换为 TextToVoiceRequest"""
        req = TextToVoiceRequest()
        req.Text = self.text if text is None else text
        req.SessionId = f"session-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        req.Codec = "wav"
        req.Volume = self.volume
        req.Speed = self.speed
        req.EnableSubtitle = self.enable_subtitle
        req.PrimaryLanguage = self.primary_language

        # 处理音色
        if self.source == "personal":
            req.VoiceType = FAST_VOICE_TYPE_ID
            req.FastVoiceType = self.voice_id
        else:
            req.VoiceType = int(self.voice_id)
        
        return req

            
