# 音遊譜面解析與訓練資料前處理 v0.1

依照「遠端訓練5060」討論製作：**osu!mania / 世界計畫譜面 + 對應歌曲 → 統一 JSON → 音訊與標籤 → 日後交給 PyTorch 訓練 → Godot / Chu Pico**。

這是一份可用 VS Code 開啟的 Python 專案。已包含可執行範例與測試，尚未訓練模型，也沒有連線或修改你的 5060 主機。

## 先跑起來

在 VS Code「檔案 → 開啟資料夾」選擇本專案資料夾，開啟 PowerShell 終端機。

首次建立環境（Python 3.10 以上；本機驗證使用 3.12）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

執行示範：

```powershell
.\.venv\Scripts\python.exe create_demo_audio.py
.\.venv\Scripts\python.exe -m rhythm build examples work/demo-dataset --features --manifest examples/manifest.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

輸出資料夾必須是新的或空的。重跑請改成 `work/demo-dataset-2` 等名稱，以免舊樣本混入新資料集。VS Code 的 F5 設定也使用此規則。

只解析譜面不需要任何套件，可以直接用系統 Python：

```powershell
python -m rhythm parse examples/mania.osu work/mania.json
python -m rhythm parse examples/sekai.sus work/sekai.json
python -m rhythm godot work/sekai.json work/godot-preview.json --lanes 4
```

`sample-output/` 是實際執行本專案得到的範例資料集，音訊是自己產生的測試聲，不是用來訓練作譜品質的正式資料。

## 正式資料怎麼放

```text
my-songs/
  song-a/
    music.mp3
    easy.osu
    hard.osu
  song-b/
    music.wav
    expert.sus
```

```powershell
.\.venv\Scripts\python.exe -m rhythm build my-songs work/my-dataset --features --manifest my-manifest.json
```

`.osu` 從 `AudioFilename` 找歌曲；`.sus` 從 `WAVE` 找歌曲。找不到時可由 manifest 指定，不會隨便猜一首音訊配上去。

世界計畫的原始 SUS 可能沒有歌名、難度、音檔路徑；這些請在 manifest 補齊。manifest 的鍵是相對輸入資料夾的譜面路徑，使用 `/`；`audio` 則相對於那張譜面所在的資料夾，也可用絕對路徑。

```json
{
  "charts": {
    "song-b/expert.sus": {
      "song_id": "song-b-original",
      "audio": "music.wav",
      "difficulty": "expert",
      "audio_time_shift_seconds": 0,
      "source_url": "填實際來源",
      "license": "填實際授權；不清楚就填 unknown"
    }
  }
}
```

同一首歌的不同難度、不同遊戲版本，請使用同一個 `song_id`。音檔若是完整版、剪輯版或不同編碼，不能只靠檔名辨識。改變音訊剪輯內容時，還要確認標籤是否對齊。

## 支援範圍

| 輸入 | 初版行為 |
| --- | --- |
| osu!mania `.osu`，Mode 3 | tap、hold、1–18 軌、和弦、BPM 變化、SV 資料保留 |
| 世界計畫 `.sus` 常見譜面 | tap、critical、flick 方向、trace、slide 起終點與中繼／控制節點、BPM、小節長度與 MEASUREBS |
| 世界計畫 12 軌 | 原始 lane 2–13 轉成 0–11，保留寬度與正規化中心位置 |
| SUS 裝飾與速度 | guide、非遊玩區事件、視覺指令另存，不作為一般打擊標籤 |
| 不支援的特殊音符 | 例如隱藏起終點 7/8、非標準方向、其他 SUS hold 家族，記錄錯誤並拒收該張譜 |
| `.osz` | 請先用解壓縮程式解開，再指定資料夾 |
| Unity AssetBundle、加密資源、Sonolus JSON、USC、影片／截圖 | 本版不解析 |

本版以常見的世界計畫 SUS profile 為目標，並非所有 SUS 遊戲或所有新版本譜面都相容。特殊譜面需拿實際檔案做對照驗證；目前驗證使用人工合成 fixture，尚未對大量真實譜面做相容性測試。

SUS 的 tap、directional 與 slide 可能在同一時間／左端軌道重疊，它們會合併成同一事件的屬性，避免把上滑判成兩顆獨立 note。滑條每個 path 節點都有時間、位置、寬度、role 及 flick 資訊；初版沒有重建遊戲引擎的曲線插值或連擊 tick。

## 統一 JSON

時間都是**歌曲音訊起點的秒數**，lane 從 0 開始。例如：

```json
{
  "time": 1.25,
  "lane": 2,
  "width": 1,
  "position": 0.625,
  "width_normalized": 0.25,
  "type": "hold",
  "end_time": 2.0,
  "duration": 0.75
}
```

- `type`：`tap / hold / flick / trace / slide`，JSON 保留來源語意。
- `position`：`(lane + width/2) / key_count`，跨遊戲位置可比較。
- `timing`：每段 BPM 的 beat 與 audio time；osu SV 不當成 BPM。
- `source_game / difficulty / key_count`：作為日後的條件輸入。
- `provenance / source_path / chart_id`：来源、授權紀錄與 SHA-256。
- `song_group / split`：歌曲分組及 train / validation / test。

SUS `WAVEOFFSET` 的正值代表音訊比譜面晚開始，所以 **audio_time = chart_time - WAVEOFFSET**。額外的 `audio_time_shift_seconds` 再加在結果上：正值把所有 note 延後。變速不改變歌曲本身的取樣時間。

## 訓練輸出

```text
dataset.jsonl          每張譜的來源、分組、特徵檔清單
charts/<sha256>.json   完整事件／路徑資料
features/*.npz        8 秒一段的特徵與標籤
report.json           成功／失敗／重複／分組統計
```

預設音訊轉 mono、22050 Hz，FFT 2048、hop 441（20 ms）、80 個 HTK Mel bands，三角濾波器以頻寬正規化，功率頻譜取 `log1p`。FFT 使用置中窗口，frame k 的時間是 `k × 0.02` 秒。這是離線作譜資料，會利用該時間前後的音訊。

每個 NPZ：

| 欄位 | 預設形狀 | 用途 |
| --- | --- | --- |
| `x` | `[400, 80]` float32 | 聲音特徵 |
| `y` | `[400, 7]` float32 | 多標籤事件與長按狀態 |
| `onset` | `[400]` float32 | 第一階段「此時需要新的打擊動作嗎」 |
| `mask` | `[400]` bool | 真實音訊 frame，排除尾端補零 |
| `frame_times` | `[400]` | 絕對音訊秒數；補零位置必須看 mask |

七個標籤依序是 `tap, hold_start, hold_end, air, trace, slide_tick, hold_active`。`air` 由來源 flick 映射，這是你遊戲的訓練設計，不是世界計畫原本的 AIR 操作。`slide_tick` 只代表可見中繼節點；`hold_active` 表示至少一條長按／滑條正在進行，不是逐軌道的手指軌跡。

`onset` 是 tap、hold_start、air 的聯集，不含 release、trace 和滑條中繼點。20 ms 內相同類型的事件或和弦會合併成 1；合併數記錄在 `label_report`，逐顆事件仍在 JSON。這一版 NPZ 用於「時機＋類型」，尚未做逐軌道位置張量或序列 token。

長按狀態採 `[start, end)`；跨 8 秒邊界的長按會延續至下一片段，但不重複製造 hold_start。負時間／超出音訊長度的打擊標籤會拒絕該譜的特徵輸出，請修正音訊或 offset，JSON 仍保留供檢查。

音訊使用 soundfile 解碼；WAV/FLAC/OGG 等依其 libsndfile 支援，MP3 若無法解碼可先轉成 WAV。本版不含自動音訊下載或格式轉檔工具。

## 如何接 PyTorch

安裝 PyTorch 前請在 5060 電腦依它的 CUDA 環境另外處理；這份前處理工具不需要 GPU。

```python
from rhythm.dataset import RhythmDataset
from torch.utils.data import DataLoader

dataset = RhythmDataset("work/my-dataset", split="train")
loader = DataLoader(dataset, batch_size=8, shuffle=True, num_workers=0)
for batch in loader:
    x = batch["x"]          # [batch, 400, 80]
    y = batch["onset"]      # 第一階段 timing 任務
    mask = batch["mask"]    # loss 必須排除補零 frame
    break
```

`RhythmDataset` 的 NumPy 讀取流程已測試；上面的 PyTorch adapter 範例尚未在這台電腦執行，沒有為了前處理安装大型 CUDA / PyTorch 套件。正式訓練需額外建立模型、loss、類別不平衡處理與評估流程。

歌曲分組會合併相同 `song_id`、音訊 SHA-256、歌名＋作者、遊戲歌曲／譜面集 ID 的關聯。同組所有難度與所有片段只屬於同一 split。完全無法辨識的歌曲會保守地放在同一組並警告。

使用 hash 約 80/10/10 分組，少量歌曲可能沒有 validation 或 test；範例只有一首，因此不足以評估模型。這不是音樂指紋辨識：不同剪輯、不同編碼又缺少一致 metadata 時，請手動填相同 `song_id`。增加曲目或改動 metadata 後應重新固定整份資料集版本，分組可能改變。

## Godot 預覽

`godot` 命令輸出 `tap / hold / air` 和指定軌道數，適合先接 JSON 讀取流程。滑條降成固定軌長按、trace 省略，上滑保留為 AIR；減少軌道可能造成衝突，輸出會提示。這是簡化預覽，**不是已完成的可玩譜面生成器**，也不拿這種降階結果當本版訓練標籤。

## 檔案導覽

- `rhythm/parsers.py`：兩種譜面格式與正規化。
- `rhythm/features.py`：音訊 Mel、frame 標籤與切段。
- `rhythm/pipeline.py`：CLI、批次、分組、來源紀錄、Godot 預覽。
- `rhythm/dataset.py`：供訓練端讀取的 Dataset。
- `tests/`：格式、時間／offset、上滑合併、BPM、分組與音訊對齊測試。
- `examples/`：自己製作的範例譜面及 manifest。
- `.vscode/`：除錯與測試設定。

批次有任何失敗會回傳 exit code 1，命令／輸出路徑錯誤回傳 2，完整成功回傳 0；請檢查 `report.json` 再把資料送進訓練。

## 格式參考

實作查核日期：2026-09-08。

- [osu! 官方 .osu 格式](https://osu.ppy.sh/wiki/en/Client/File_formats/osu_%28file_format%29)
- [SUS 原始作者 v2.7 規格](https://gist.github.com/kb10uy/c171c175ba913dc40a73c6ce69da9859)
- [世界計畫 SUS 社群擴充觀察](https://gist.github.com/YumYummity/49935bf74367712b6ba240474cb7ce22/01bb4a36dfc27e7927a9e416e02c3c43b310e135)（作者也標示部分行為尚未確認，不能視為官方保證）
- [pjsekai-scores-rs 原始碼與測試](https://github.com/Team-Haruki/pjsekai-scores-rs)

附帶合成範例標為 CC0；其他輸入檔案的權利資訊照實記錄，不會把第三方譜面自動標成開源。
