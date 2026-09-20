# 第三环节：工程文件导出 v0.1

## 1. 目标

第三环节负责把第二环节已经确定的虚拟剪辑时间线，翻译成剪辑软件能够导入和继续修改的工程文件。

本环节不判断“应该删什么”，也不重新计算剪辑边界。它必须忠实执行 `virtual_timeline.json` 中的保留片段映射，确保导入 DaVinci Resolve 或 Premiere Pro 后，剪辑点与虚拟时间线逐帧一致。

## 2. 当前输入与输出

输入：

- 第二环节输出的 `virtual-timeline-v0.1`
- 原素材路径、帧率、分辨率、音频采样率和声道数
- 时间线名称

当前输出：

- Final Cut Pro 7 XML（FCP7 XML）
- 可导入 DaVinci Resolve
- 可导入支持 FCP7 XML 的 Premiere Pro 工作流

未来可增加：

- AAF
- EDL
- FCPXML
- 直接渲染视频

## 3. 导出设施

1. **标准时间线读取器**：只读取第二环节确定的保留片段，不重新推断删除范围。
2. **素材描述生成器**：写入素材文件名、路径、总帧数、分辨率、帧率和音频参数。
3. **视频轨道生成器**：按照 `source_in/source_out` 和 `timeline_start/timeline_end` 建立视频片段。
4. **音频轨道生成器**：为每个声道建立与视频完全相同的片段映射。
5. **波纹结果保持器**：确保所有保留片段在新时间线上首尾相接。
6. **工程格式序列化器**：生成符合 FCP7 `xmeml` 结构的 XML。
7. **可追溯命名器**：素材名、候选版本和格式体现在文件名及时间线名称中。
8. **导出后验证器**：检查 XML 片段数量、源素材入出点、时间线位置和总时长。

## 4. 核心约束

- 第三环节不得修改第二环节的任何剪辑决定。
- 所有时间均使用整数帧，禁止用浮点秒直接写入 XML。
- 素材区间统一采用 `[source_in, source_out)`。
- 时间线上的视频与音频必须使用同一组剪辑点。
- 每个片段的源长度必须等于它在新时间线上的长度。
- 前一个片段的 `timeline_end` 必须等于后一个片段的 `timeline_start`。
- XML 总时长必须等于虚拟时间线总时长。
- 未通过验证时不得交付给剪辑软件。

## 5. 标准运行

```bash
python3 -m decision_pipeline.fcp7_xml \
  outputs/example/virtual_timeline.json \
  outputs/example/SmartCut_候选版_FCP7.xml
```

核心实现位于：

```text
decision_pipeline/fcp7_xml.py
```

## 6. 在 DaVinci Resolve 中导入

1. 打开 DaVinci Resolve 项目。
2. 选择“文件”。
3. 选择“导入时间线”。
4. 选择“导入 AAF、EDL、XML”。
5. 选择 SmartCut 生成的 FCP7 XML。
6. 检查素材链接、时间线总时长、视频轨道、音频轨道和剪辑点。

如果原素材移动了位置，DaVinci Resolve 可能要求重新链接媒体。这不代表剪辑点错误。

## 7. 当前验证状态

本项目已使用一条50fps中文口播视频完成真实验证：

- FCP7 XML 可以成功导入 DaVinci Resolve。
- 视频和音频片段能够正确建立。
- 已验证的人工剪辑点可以逐帧一致还原。
- 独立候选虚拟时间线可以正确转换为工程文件。
- 工程总时长与第二环节虚拟时间线一致。

## 8. 三个环节的责任边界

```text
第一环节：决定删什么
        ↓ edit-decisions
第二环节：决定从哪一帧删到哪一帧，并生成波纹时间线
        ↓ virtual-timeline
第三环节：把时间线翻译成剪辑软件工程格式
        ↓ FCP7 XML / AAF / EDL
DaVinci Resolve 或 Premiere Pro：人工复核与精修
```

这种分层保证导出格式发生变化时，不需要重新做内容判断；剪辑规则发生变化时，也不需要修改 XML 生成逻辑。
