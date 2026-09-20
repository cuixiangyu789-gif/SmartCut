# 第一环节：剪辑决策流水线 v0.1

> 模型选型已锁定：豆包语音识别大模型负责逐字转录，当前项目中的 Codex 模型负责依据 SOP 制定剪辑决策。MLX Whisper 不属于正式方案，不得作为自动降级路径。

## 已实现的八个环节

1. **逐词转录接入**：接收豆包语音识别结果，并为每个词分配稳定 `word_id`。
2. **声音事件检测**：对 16-bit PCM WAV 检测静音区间。
3. **长视频分块**：按词数分块并保留重叠上下文。
4. **剪辑决策执行器**：生成完整提示词，并由当前项目中的 Codex 模型输出结构化决定。
5. **结构校验**：检查 action、category、词语范围、原因和置信度。
6. **决策合并**：去除重叠分块产生的重复决定，将删除/保留冲突转为人工复核。
7. **人工复核报告**：生成包含内容、原因、定位和置信度的 Markdown 报告。
8. **评测与案例库**：计算删除准确率、召回率和误删，并附带可运行测试案例。

## 运行要求

- Python 3.11 或更新版本。
- 核心流程仅使用 Python 标准库。
- 正式 ASR 固定为豆包服务，剪辑判断固定由当前项目中的 Codex 模型执行。
- 凭证通过环境变量或本机安全凭证存储提供，不得写入项目。

## 快速测试

```bash
python3 -m unittest discover -s tests -v
```

## 1. 豆包逐词转录

豆包适配器接收语音识别结果后，将其规范化为项目内部结构。若豆包服务不可用，任务直接失败，不切换到其他模型。

## 2. 规范化逐词转录

```bash
python3 -m decision_pipeline.pipeline normalize-transcript \
  fixtures/sample_whisper_transcript.json \
  fixtures/sample_media.json \
  outputs/sample_transcript.json
```

## 3. 提取音频并检测静音

先从视频提取 16kHz 单声道 16-bit PCM WAV：

```bash
.venv/bin/python -m decision_pipeline.pipeline extract-audio \
  /path/to/video.mp4 outputs/input.wav
```

再检测静音：

```bash
python3 -m decision_pipeline.pipeline detect-silence \
  input.wav outputs/audio_events.json
```

## 4. 对长视频分块

```bash
python3 -m decision_pipeline.pipeline chunk \
  outputs/sample_transcript.json outputs/chunks.json \
  --max-words 800 --overlap-words 100
```

## 5. 构建模型提示词

先从 `chunks.json` 取出单个分块保存为 JSON，然后运行：

```bash
python3 -m decision_pipeline.pipeline build-prompt \
  chunk.json outputs/audio_events.json outputs/prompt.txt
```

## 6. 调用剪辑决策模型

```bash
# 在当前 Codex 项目中读取生成的提示词并输出结构化剪辑决定。
```

剪辑判断固定使用当前项目中的 Codex 模型，输出受到 `decision_pipeline/edit_decisions.schema.json` 的结构约束。

## 7. 校验输出

```bash
python3 -m decision_pipeline.pipeline validate \
  outputs/model_decisions.json outputs/sample_transcript.json
```

## 8. 合并多个分块的结果

```bash
python3 -m decision_pipeline.pipeline merge \
  outputs/sample_transcript.json outputs/merged_decisions.json \
  outputs/chunk_1_decisions.json outputs/chunk_2_decisions.json
```

## 9. 生成人工复核报告

```bash
python3 -m decision_pipeline.pipeline report \
  outputs/merged_decisions.json outputs/sample_transcript.json \
  outputs/review_report.md
```

## 10. 与人工标准答案比较

```bash
python3 -m decision_pipeline.pipeline evaluate \
  outputs/model_decisions.json fixtures/sample_decisions.json \
  outputs/evaluation.json
```

## 当前边界

- 豆包 ASR 接入需要火山引擎资源标识和鉴权凭证。
- 工程验证时期产生的本地 Whisper 文件不属于正式流程。
- 画面分析尚未纳入 v0.1。
- 内容决策结果尚未自动转换为最终帧边界；该工作属于第二环节的边界校准器。
- 在没有人工复核前，`review` 永远不会自动删除。
