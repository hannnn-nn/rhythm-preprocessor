# 初版驗證結果

日期：2026-09-08。環境：Windows、Python 3.12.10；套件版本見 requirements-tested.txt。

- `python -m unittest discover -s tests -v`：15 項測試全部通過。
- `python -m rhythm parse examples/sekai.sus ...`：成功產生統一 JSON。
- `python -m rhythm godot ... --lanes 4`：成功保留滑條尾端 AIR，生成簡化預覽。
- `python -m rhythm build examples sample-output/dataset --features --manifest examples/manifest.json`：2 張譜成功、0 失敗、2 個 NPZ 視窗。
- 兩張合成譜共用 1 首音訊，確實分在同一組。此示範被 hash 分到 test，train/validation 為空，不能用這份示範评估或訓練正式模型。

測試涵蓋 osu 軌道邊界、長按、BPM/SV 分離；SUS 小節長度、BPM 段落積分、offset 正負、上滑疊合、slide channel 重用、未知音符拒收；音訊 resample 與 frame 對齊、負時間標籤拒收、長按區間、尾段 mask、批次失敗報告、歌曲關聯分組和 Dataset 讀取。

界限：使用自行製作的譜面與音訊 fixture；未對大量真實遊戲譜面做對照、未在 5060 上訓練、未執行 PyTorch DataLoader、未接 Godot 實際遊玩。
