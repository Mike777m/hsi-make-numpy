# hsi-make-numpy

ハイパースペクトルカメラ AG-HSV10M（420〜700 nm・29 バンド）の再構成結果（band01〜29.png）を、
選んだ RAW 名の分だけ 1 枚ずつ `.npy`（29 バンドの配列）にするツール。

この配布版の既定は **白板補正なし・黒補正なし**。npy の値は再構成の PNG の値そのもの。

---

## 1. 準備

Python 3.10 以上が必要。

```bash
git clone <このリポジトリの URL>
cd hsi-make-numpy
pip install -r requirements.txt     # numpy と opencv-python
```

## 2. データを置く

リポジトリのフォルダの中に、次のように置く（場所は `make_numpy/settings.py` で変えられる）。

```
hsi-make-numpy/
├─ raw_data/<日付>/<時間>/<RAW名>.RAW                                  ← RAW
├─ input_folder/<日付>/<時間>/<RAW名>/reconstruct/<RAW名>/band01〜29.png ← 再構成結果
├─ names.txt                                                          ← npy にする RAW 名
├─ hsi2rgb/        （コード）
└─ make_numpy/     （コード・設定）
```

* **RAW ファイルも必要**。中身は読まないが、RAW 名から「どの日付・時間フォルダにあるか」を探すのに使う。
  RAW が無い名前は「RAW が見つからない名前」として飛ばされる。
* 再構成結果は、Iromiel Hyper の出力の形 `input_folder/<日付>/<時間>/reconstruct/<再構成日時>/band01〜29.png`
  でもよい（そのときは RAW と名前順で対応させる）。
* `names.txt` は 1 行に 1 つ RAW 名を書く（`names.example.txt` を参照）。
  `00800725.RAW` のように拡張子付きでもよい。`meta_data.json` や `["00800725", ...]` の JSON も使える
  （そのときは `settings.py` の `NAMES_FILE` を書き換える）。

## 3. 実行

```bash
python make_numpy/main.py
```

`output_npy/<RAW名>.npy` ができる。最後に、作れなかった名前が理由ごとに表示される。
もう一度実行すると、npy が既にあるものは飛ばす。

## 4. できる npy

| 項目 | 値 |
|---|---|
| 形 | `(29, 高さ, 幅)`。FHD なら `(29, 1440, 1920)`、1 枚 約 320MB |
| 型 | `float32` |
| バンド | `npy[0]` = 420 nm、`npy[1]` = 430 nm、…、`npy[28]` = 700 nm（10 nm 刻み） |
| 値 | 白板補正なしなら、再構成の PNG の値そのもの（整数値。0〜数千程度） |

```python
import numpy as np

cube = np.load("output_npy/00800725.npy")   # (29, H, W)
wavelengths = [420 + 10 * i for i in range(29)]
spectrum = cube[:, 700, 900]                 # 画素 (x=900, y=700) のスペクトル
```

---

## 5. 設定（`make_numpy/settings.py`）

| 設定 | 意味 |
|---|---|
| `RAW_ROOT` | RAW のフォルダ（`<RAW_ROOT>/<日付>/<時間>/<RAW名>.RAW`） |
| `RECONSTRUCT_ROOT` | 再構成結果のフォルダ（`<RECONSTRUCT_ROOT>/<日付>/<時間>/`） |
| `DATES` | 探す日付。例 `["10082026", "10092026"]`。空 `[]` なら全部 |
| `NAMES_FILE` | npy にする RAW 名のファイル |
| `OUTPUT_DIR` | npy の保存先 |
| `BLACK_CORRECTION` / `WHITE_CORRECTION` | 補正（下の表） |
| `OVERWRITE` | 既にある npy も作り直すか |

別のドライブなどを絶対パスで書くときは、`Path("D:/data/raw_data")` のように **`Path(...)` で囲む**
（文字列のままだと `'str' object has no attribute 'mkdir'` などのエラーになる）。

### 補正

| `BLACK_CORRECTION` | `WHITE_CORRECTION` | npy の中身 | 必要なもの |
|---|---|---|---|
| False | False | 再構成の値そのもの（**既定**） | なし |
| True | False | 再構成の値 − ダーク | `DARK_DIR`（ダークの band01〜29.png） |
| False | True | 白板で割った反射率 | `CALIBRATION_JSON`（白板ROI `{"RAW名": [x0, y0, x1, y1]}`） |
| True | True | ダークを引いてから白板で割った反射率 | 両方 |

白板補正なしで作った npy も、白板ROI が分かればあとから反射率にできる。

```
反射率 = 0.9 × npy[b] ÷ (白板ROI内の npy[b] の平均)      ※ 0.9 は白板の反射率
```

### 再構成（`RECONSTRUCT_MISSING`）

再構成結果が無い RAW を再構成するには、Iromiel Hyper SDK・NVIDIA の GPU・元のプロジェクトの
`automation/` フォルダが必要。この配布版では `False`（再構成結果が無い名前は飛ばして表示するだけ）。

---

詳しい処理の流れは `make_numpy/説明.md` を参照。
