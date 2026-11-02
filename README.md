# ccplayer-patch — cc播放器"去抢焦点"自动补丁流水线

对 cc播放器（CCMusicModuleReborn / com.codex.ccplayer，TL 改版）的原版 deb 做字节级补丁并自动重打包。
全程在 GitHub Actions（ubuntu）上运行，不需要本地 Mac / Theos。

## 这个补丁改了什么

| 架构 | 位置 | 改动 | 效果 |
|---|---|---|---|
| arm64 | __text 0x52704（文件偏移 0x5A704） | `BL -> ccm_launchMusicAppForIdlePlayRequest` 替换为 `NOP` | 点播放键且当前无播放时，不再自动启动/恢复音乐 App |
| arm64e | __text 0x53784（文件偏移 0x10B784） | 同上 | 同上 |

效果：模块永远不会自动打开 QQ音乐/网易云/汽水/酷我等音乐 App 去抢占系统音频焦点，
消除"有其他软件使用音频，自动暂停播放"这一现象的来源。

背景视频/图片：原代码本身就是"去音轨 + 音频归零 + setMuted:YES + setVolume:0.0"，素材默认无声，**无需改动**。

安全机制：补丁前会逐字节断言两处指令与已验证版本完全一致；任何一处不匹配（说明 deb 版本/布局不同）
就立即失败退出，**绝不盲改**。

## 使用方法

1. **放入原版 deb**：把你要补丁的原版 deb 上传到仓库 `input/` 目录，命名为 `original.deb`
   （网页端：仓库页 → Add file → Upload files，路径保持 `input/original.deb`）。
2. **触发流水线**：仓库 Actions 页 → 左侧选 `ccplayer-patch` → 右侧 **Run workflow**
   （`deb_path` 保持默认即可；也可填其他仓库内路径）。
3. **获取产物**：跑完后进本次运行页面 → Artifacts 下载 `patched-deb`；
   若 push 分支是 main，还会自动生成 Release，附带的 deb 可直接下载。
4. **真机安装**：把产物 deb 传到 iPhone，用 Sileo / Zebra / Filza 覆盖安装，然后按 `CHECKLIST.md` 验收。

## 目录结构

```
ccplayer-patch/
├── .github/workflows/ccplayer-patch.yml   # GitHub Actions 流水线（手动触发）
├── scripts/patch_deb.py                   # 解包→字节断言→补丁→重打包（纯 Python 标准库）
├── input/                                 # 放原版 deb（original.deb）
├── output/                                # 产物 deb（流水线生成，勿手动提交）
├── README.md
└── CHECKLIST.md                           # 真机验收清单
```

## 说明

- 产物版本号在原版基础上自动递增（如 1.0 → 1.0-1），Sileo 会识别为新版本，可直接覆盖安装。
- 安装后 postinst 会重置授权状态（重新弹一次授权）；已导入的背景素材目录不受影响。
- 想回滚：直接装回原版 deb 即可。
