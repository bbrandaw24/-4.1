# 草莓生长阶段数据集

本目录用于 Day 5 图像分类数据集。图片按 `train`、`val`、`test` 划分，并按四个阶段放入对应类别目录：

```text
train/{germination,flowering,fruit_set,ripening}/
val/{germination,flowering,fruit_set,ripening}/
test/{germination,flowering,fruit_set,ripening}/
reject/
```

当前目录只提供结构占位文件，不包含未经授权的真实农场图片。建议先按植株或采集日期分组，再按 70%/15%/15% 划分，避免相邻视频帧泄漏到不同集合。

每张图片进入训练集前应完成质量审核，并记录 `image_id`、来源、采集时间、标注人和审核状态。无法唯一判断阶段、严重遮挡或质量不合格的图片放入 `reject`。
