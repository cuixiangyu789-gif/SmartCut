# 第二环节：执行剪辑决策与虚拟时间线 v0.2

## 1. 目标

第二环节不再判断“该不该删”，而是把第一环节的内容决定稳定地转换为整数帧删除区间，并建立一条可验证、可导出、可回溯的虚拟时间线。

输入是素材信息、规范化逐字稿、声音事件、剪辑决定和可选人工覆盖；输出固定为 `virtual-timeline-v0.1`。FCP7 XML 属于第三环节，不在本环节直接生成。

## 2. 八个执行设施

1. **决策定位器**：支持文字范围和声音事件范围两类定位。
2. **边界校准器**：根据前后词语和静音，为删除内容保留自然连接余量。
3. **人工精确覆盖**：人工确认过的帧边界优先级最高，禁止被算法重新计算。
4. **整数帧吸附**：所有秒数统一转换为 `[start_frame, end_frame)`。
5. **删除区间合并**：合并重叠或相邻删除范围，避免碎片和重复切割。
6. **波纹时间线计算**：计算每个保留片段的原片区间和新时间线区间。
7. **质量检查**：验证范围合法、无重叠、总帧数守恒以及音视频共用同一片段表。
8. **反馈与评测**：记录人工调整前后边界、误差帧数、调整原因和规则归属。

## 3. 边界优先级

从高到低依次为：

1. 人工确认的精确帧覆盖。
2. 已确认的声音事件边界。
3. 词语时间戳与相邻语音间隙推导。
4. 保守的词首、词尾小幅扩展。

不得用低优先级结果覆盖高优先级边界。

## 4. 默认连接余量

- 删除中间一整段重说时，在前一句后保留约 `0.20s`，在下一句前保留约 `0.20s`。
- 删除片尾废话时，在最后完整句后默认保留 `0.50s` 尾气。
- 纯静音删除默认采用声音事件边界；短句内停顿必须先由第一环节决定是否删除。
- 所有余量最终吸附到原素材帧率。

这些数值是边界策略，不是内容规则；必须通过更多人工样本统计后再升级版本。

## 5. 标准运行

```bash
python3 -m decision_pipeline.timeline_engine \
  --media outputs/doubao_asr_test_video/media.json \
  --transcript outputs/doubao_asr_test_video/normalized_transcript.json \
  --audio-events outputs/doubao_asr_test_video/audio_events.json \
  --decisions outputs/doubao_asr_test_video/codex_decisions_v0.1.json \
  --overrides outputs/doubao_asr_test_video/boundary_overrides.json \
  --sequence-name "AI自动粗剪_测试视频_执行引擎版" \
  --output outputs/doubao_asr_test_video/virtual_timeline.json
```

第三环节随后只读取 `virtual_timeline.json`：

```bash
python3 -m decision_pipeline.fcp7_xml \
  outputs/doubao_asr_test_video/virtual_timeline.json \
  outputs/doubao_asr_test_video/AI自动粗剪_测试视频_执行引擎版_FCP7.xml
```

面向使用者的最终 XML 统一复制到 `/Users/cuixiangyu/Desktop/AI自动剪辑导出/`。项目内的 `outputs/` 版本作为过程归档，桌面版本用于直接导入剪辑软件。固定位置与命名规则记录在 `export_settings.json`。

## 6. 当前验收标准

- 所有人工确认的边界必须逐帧一致。
- 同一输入重复执行得到完全相同的虚拟时间线。
- 删除区间和保留区间必须完整覆盖原素材且互不重叠。
- 输出必须保留每个边界的来源、方法和关联决定，方便回溯。
- 未通过质量检查时不得进入第三环节。

## 7. 当前样片状态

已用桌面的 `测试.mp4` 完成 v0.1 验证：6 个删除区间生成 5 个保留片段，原片 3796 帧，波纹后 2291 帧（50fps 下为 `00:00:45:41`）。帧守恒、时间线连续、范围合法和音画切点一致均通过。

当前边界中，4 段来自人工确认的精确帧；另外 2 段由逐字稿和连接余量计算。执行结果详见 `outputs/doubao_asr_test_video/第二环节_执行验证报告.md`。

## 8. v0.2 新增的四项边界能力

### 8.1 波形级安全帧吸附

传入 `--audio-wav` 后，引擎会计算每一视频帧对应音频的 RMS 响度，只在候选边界附近寻找达到静音阈值的帧。若附近没有安全静音帧，则保留原边界，不强行移动。

### 8.2 呼吸与音节保护

逐字稿中每个词的起止时间会扩展为保护区。波形吸附不得把切点移动到保护区内，从而降低截断句首辅音、句尾尾音和紧邻呼吸的风险。每次调整都会写入 `boundary_diagnostics`。

### 8.3 边界错题本与结构化反馈

- 人类可读错题本：`第二环节_边界错题本.md`
- 机器可读日志：`outputs/doubao_asr_test_video/boundary_feedback_log.json`
- 记录工具：`python3 -m decision_pipeline.boundary_feedback`

反馈同时保存自动边界、人工边界、两端误差帧数、问题类型、调整原因和所用节奏档位。单个案例不直接修改通用参数。

### 8.4 个性化节奏档位

参数位于 `editing_pace_profiles.json`：

- `natural`：保留较多呼吸与句间空间。
- `compact`：自然紧凑，当前默认。
- `energetic`：更强节奏，仅在用户明确选择时使用。

标准波形校准运行示例：

```bash
python3 -m decision_pipeline.timeline_engine \
  --media outputs/doubao_asr_test_video/media.json \
  --transcript outputs/doubao_asr_test_video/normalized_transcript.json \
  --audio-events outputs/doubao_asr_test_video/audio_events.json \
  --decisions outputs/doubao_asr_test_video/blind_decisions_v0.1.json \
  --overrides outputs/doubao_asr_test_video/blind_no_overrides.json \
  --audio-wav outputs/doubao_asr_test_video/source_audio.wav \
  --profile compact \
  --output outputs/doubao_asr_test_video/waveform_refined_virtual_timeline.json
```

当前样片的4个自动边界都已处在满足阈值的安全帧，因此波形校准记录为0帧调整。这说明它没有为了显示“工作过”而无意义地移动正确边界。
