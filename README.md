# 语音合成插件

这是一个用于 robot cli 的语音合成插件

## python 依赖要求
- `tencentcloud-sdk-python` 版本 3.1.129 或更高
- `python-dotenv` 版本 1.0.0 或更高

## 安装步骤

```bash
# 1. 在插件目录（即 zccr config 中设置的插件目录）中执行以下命令
git clone https://github.com/fungqy/robot-tts-plugin.git
cd robot-tts-plugin

# 2. 使用 uv 时，执行以下命令安装依赖
uv pip install -r requirements.txt

# 2. 使用 conda 时，执行以下命令安装依赖
# 激活 conda 环境
# conda activate <your_env_name>
# 安装依赖
# conda install --file requirements.txt

# 3. 配置腾讯云访问密钥
cp .env.example .env
# 编辑 .env 文件，添加你的腾讯云访问密钥
# TENCENT_ACCESS_SECRET_ID=your_secret_id
# TENCENT_ACCESS_SECRET_KEY=your_secret_key
```

## 支持的参数

参数 | 类型 | 默认值 | 说明 
--- | --- | --- | --- 
text | str |  | 必填 待合成文本 
save_path | str |  | 必填 保存目录的绝对路径 
voice_id | str | "101008" | 音色 ID 
source | str | "offical" | 音色来源, offical 或 personal 
volume | float | 0 | 音量 [-10, 10] 
speed | float | 1.0 | 语速 [-2, 6] 
primary_language | int | 1 | 主语言, 1 或 2 (1=中文, 2=英文)
sample_rate | int | 16000 | 采样率, 16000 或 8000 
enable_subtitle | bool | True | 是否生成字幕, True 或 False


## 用法示例

### 示例1: 通过 zccr 执行

```bash
zccr text_to_voice --run '{"text":"你好,小谷", "save_path": "/tmp/output"}'
```

### 示例2: 执行脚本

```bash
python scripts/invoke.py <<'EOF'
{"parameters": {"text": "你好世界，这是一段测试文本", "save_path": "/tmp/output"}}
EOF
```

## 输出示例

- 成功: 
```json
{
    "success": true, 
    "audio_file": "/tmp/output/e7050e5c-9b72-4651-bf2b-a0e162b1ba00.wav", 
    "subtitle_file": "/tmp/output/e7050e5c-9b72-4651-bf2b-a0e162b1ba00.json"
}
```

- 失败: 
```json
{
    "success": false, 
    "message": "volume(音量) 必须在 [-10, 10] 范围内"
}
```

