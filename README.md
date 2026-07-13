# Git Repository Template

ネットワーク自動化開発における開発環境のベストプラクティスをTemplate化したものです
基本的にこのRepositoryをTemplateとしてRepositoryを新しく作成してCloneする運用を考えています。

## 含まれてるもの

### devcontainer.json
devcontainerは必須の利用とした方が良いので、含んでいる。
もし、利用しない場合はディレクトリごと削除してもらえれば問題ないが、利用するのを強く推奨する。imageは基本的にはpythonが便利なのでpython3.12-bulls-eyeとしているが、適宜変更して利用する。

### .gitignore
pythonに関連＋env関連のgitignoreを記載。

### requirements.txt
devcontainerのpostCreateに`pip install -r requirements.txt`を組み込んでいるので標準搭載。Poetryも検討したがまずはpipでStartした方がわかりやすいのでpipを採用した

### Taskfile.yml
Task Runnerとして非常に優秀なgo-taskを採用しており、devcontainerのpostCreateでも利用しているため標準搭載としている。
また、ネットワーク系の開発を実施する場合`task+peco`でログインマクロのように取り扱うのが使い勝手が良いので、sampleも記載している。

### .env.sample
装置への接続情報Credentialは基本的にGitにCommitしたくないので.envを利用する。
composeを使うときやpython dotenvを使う時に使い勝手がよいので、.envを標準搭載している

### feature/Extension
便利そうなものを記載しているので、devcontainer.jsonを参照

# 使い方やメンテナンスについて

[Confluence](https://sonync.atlassian.net/wiki/spaces/Networkwiki/pages/100008609/Dev+Containers)を参照

## ROADM Reachability Simulator

このRepositoryには、ROADMリングネットワークにおける波長到達性を評価するシミュレータを追加しています。

### 対応している入力
- DCOモジュール仕様: `json` (speed/modulationごとの最大損失・最大距離)
- ROADM仕様: `json` (vendor/model/node_loss_db)
- ルート一覧: `csv` または `xlsx` (`fiber_type,start_site,end_site,distance_km,loss_db,bypass_sites`)
- 波長計画: `json` (100G/400G coherent想定、speed/modulation/source/destination)
- EDFA: 波長計画JSON内の `amplifiers.boost_gain_db`, `amplifiers.preamp_gain_db`

### 実行例
```bash
python roadm_reachability_simulator.py \
  --dco-spec dco_spec.json \
  --roadm-spec roadm_spec.json \
  --routes routes.csv \
  --wavelength-plan wavelength_plan.json \
  --output report.json
```
