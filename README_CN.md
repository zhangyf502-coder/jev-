# CLM / Jev System One Benchmark（完整可运行版）

版本：1.0（2026-10-13）  
适用环境：Linux x86-64、Python 3.9+、CLM 本地端口 8700、TypeSafe 兼容的 `/v1/systemone` 远端接口。纯标准库，**评测脚本不需要安装任何第三方 Python 包**。

## 1. 交付内容

| 文件 | 用途 |
|---|---|
| `data/clm_jev_1300.jsonl` | **1,300 条离线可用测试题**，完整状态、问题、候选集、标准答案与标签来源 |
| `data/clm_jev_1300.csv` | 同一套测试题，UTF-8 BOM，适合 Excel/WPS 查看 |
| `data/manifest.json` | 版本、分布、来源及局限性 |
| `data/clm_jev_1300.xlsx` | 测试集中文阅览工作簿（若下载包包含） |
| `benchmark.py` | 接口调用、断点续跑、逐条记录、指标计算、图文报告 |
| `download_public.py` | 额外下载 BoolQ 或 AG News 公开验证/测试数据（要求联网，注意许可证） |
| `generate_dataset.py` | 本项目原创合成题的可复现生成器，固定随机种子 |
| `tests/` | 单元测试与**只供验证的模拟 API**；切勿将模拟指标当成模型实测指标 |

**重要：** 本包中的 1,300 条是按明确定义规则生成的原创**合成测试题**，标准答案尚未经过独立人工专家复核。适合本地功能检查、相同条件下的回归比较和初步差异发现；**不可以称作经过独立验证的权威 Jev 公共基准，也不能据此单独声称论文级 SOTA**。请配合下方公开集、真实业务样本并核验异常案例。

## 2. 1,300 条题目分布

| 场景 | 数量 | 问题类型 |
|---|---:|---|
| 客服意图分流 | 160 | Choice |
| Agent 工具选择 | 160 | Choice |
| 运维故障响应 | 120 | Choice |
| 安全风险分流 | 120 | Choice |
| 电商订单流转 | 120 | Choice |
| 文档事实核验 | 120 | Noul |
| 语义重复问题 | 120 | Noul |
| 情感强度打分 | 100 | Score |
| 运维紧急程度 | 100 | Score |
| 候选外/拒选 | 100 | Choice |
| 候选顺序扰动 | 80 | Choice，配对样本 |
| **合计** | **1300** | **Choice 860，Noul 240，Score 200** |

中文 649 / 英文 651。扰动测试与源题组成 80 对，不属于 1,300 个统计独立样本，报告中应单列一致率。

## 3. 第一次运行（与已部署的 CLM 对接）

### 3.1 安装与检查

```bash
unzip clm_jev_benchmark_1300.zip
cd clm_jev_benchmark
python3 -V
python3 benchmark.py validate --dataset data/clm_jev_1300.jsonl

# CLM 已在宿主机 :8700 的情形：
curl -sS http://127.0.0.1:8700/health | python3 -m json.tool
curl -sS http://127.0.0.1:8700/v1/models | python3 -m json.tool

# 若评测脚本在另一容器执行，127.0.0.1 指向容器自身，
# 请用可路由的宿主机地址 / 容器服务名 / --network host（Linux），并验证能访问 8700。
```

### 3.2 先执行 20 条接口兼容性检查

```bash
python3 benchmark.py run \
  --dataset data/clm_jev_1300.jsonl \
  --endpoint http://127.0.0.1:8700 \
  --model clm-latest --name clm_probe \
  --limit 20 --workers 1 --warmup 0 --retries 1 \
  --gpus 3,4 --out results_probe
```

注意 `CLM` 服务端模型名称请以本地 `GET /v1/models` 的返回为准。目前常见配置为 `clm-latest`、`clm-raw`。本程序固定使用 `POST /v1/systemone`，**不是 `/v1/chat/completions`**。

### 3.3 全量跑 CLM 最新 head、原始 head

```bash
python3 benchmark.py run --dataset data/clm_jev_1300.jsonl \
  --endpoint http://127.0.0.1:8700 --model clm-latest --name clm_latest \
  --out results --workers 1 --warmup 5 --passes 2 --gpus 3,4

python3 benchmark.py run --dataset data/clm_jev_1300.jsonl \
  --endpoint http://127.0.0.1:8700 --model clm-raw --name clm_raw \
  --out results --workers 1 --warmup 5 --passes 2 --gpus 3,4
```

`--passes 2`：第一次和第二次重复测试同样样本，报告中增加跨次预测一致率、两次 P50 比值。第二次通常有机会重用 embedding，但**不能把它无条件称为严格冷/热缓存对照**：预先已有缓存、容器重启、缓存容量、服务内部淘汰都可能影响结果。严格缓存研究必须控制清缓存条件、单独记录服务缓存统计。

### 3.4 与 TypeSafe Jev API 比较（需要自己合法持有的 API Key）

```bash
# 禁止把密钥写死到脚本或提交 Git
export JEV_API_KEY='YOUR_OWN_KEY'

python3 benchmark.py run --dataset data/clm_jev_1300.jsonl \
  --endpoint https://api.typesafe.ai \
  --model jev-latest --name jev_latest \
  --api-key-env JEV_API_KEY --require-key \
  --out results --workers 1 --warmup 5 --passes 2
```

版本化模型名称如 `jev-preview` / `jev-1.13.0` 需先验证当前 API 是否开放；可将 `--model` 替换并为各模型设置不同 `--name`。**未提供密钥就无法在本包里凭空生成 Jev 的真实评分。**

### 3.5 生成对比报告

```bash
python3 benchmark.py compare \
  --out results --models clm_latest clm_raw jev_latest

# 若还没购买/开通 Jev Key，可以先只比较本地两套 head：
python3 benchmark.py compare --out results --models clm_latest clm_raw
```

主要输出：

- `results/clm_latest_predictions.jsonl`：逐条模型返回摘要，标准答案、预测、置信度、延迟、错误等；每次请求完成立即落盘。
- `results/clm_latest_metrics.json`：全部指标、分场景/语种/题型、门限分层、顺序扰动、跨次一致性。
- `results/clm_latest_metrics_by_category.csv`：按场景指标。
- `results/clm_latest_gpu.json`：卡 3/4 的设备全局显存及利用率采样。
- `results/clm_latest_throughput.json`：当前这批新请求的吞吐。
- `results/comparison_summary.csv`：各模型横向指标汇总。
- `results/comparison_by_category.csv`：分场景差异。
- `results/paired_case_differences.csv`：**两模型比较时**每条题谁对谁错。
- `results/comparison_report.md` / `.html`：中文可读、离线打开的对比报告。
- `results/comparison_report.json`：便于其他评测平台接入的结构化汇总。

## 4. 如何算指标

| 指标 | 计算方式与注意点 |
|---|---|
| `accuracy_all` | **正确响应数 / 总请求数**；HTTP 错误和无法解析的答复均按错处理，防止只统计成功样本导致虚高 |
| `accuracy_success_only` | 正确响应数 / 可解析请求数；仅用于定位故障，不作为主准确率 |
| `macro_f1` | 按类别计算 P、R 和 F1，所有本组出现的类别取算术平均；少数类不被大类淹没 |
| `ece_10bins` | 预测标签置信度分 10 桶，按桶占比加权的 `abs(平均置信度-实际正确率)`；越低越好 |
| `brier_multiclass_or_binary` | Noul：`(P(yes)-y)^2`；Choice/Score：对每个类的 `(p-y_onehot)^2` 求和。二元和多元的数值范围不同，**不要跨题型直接比较** |
| `score_mae` | 仅 Score：预测档位与金标准档位差的绝对值平均；越低越好 |
| `score_within_one` | Score：预测档位与金标准相差不超过 1 级的比例 |
| `latency_p50/p95/p99_ms` | **成功请求**从客户端发送到响应解析前的墙钟时延，包含公网网络时延、限流及重试影响 |
| `gate_0.90.coverage` | top-class 概率不低于 0.9 的成功请求数 / 全部请求数 |
| `gate_0.90.error_rate` | `置信度>=0.9` 的自动放行样本中错误占比；覆盖量很小时须注意统计不稳定 |
| `repeat_consistency` | `--passes>=2` 时，同 ID 各次推理预测完全一致的比例 |
| `option_order_consistency` | 固定原题/重排选项两次结果相同的比例 |
| `qps_new_requests` | 本次实际新完成请求数 / 测量批次墙钟秒数，按 `--workers` 设定统计 |

**Score 约定：** API 返回每档概率时使用 argmax 确定离散档位；没有概率时对期望 `score` 四舍五入。多档期望分数并不等于最大概率档位，这个规则已固定以保证可复现。

**缺失置信度处理：** 缺少可验证的类概率时不把任意数值强行当概率，ECE/Brier 只在具有合法概率的样本上计算（样本覆盖量需同时核对）。

## 5. 高并发、硬件与公平性注意事项

1. **T4 物理卡 3/4**：`--gpus 3,4` 只是读取 `nvidia-smi` 的设备 3、4 指标，不控制 CLM 部署卡位。GPU 分配由你现有 Docker/NVIDIA runtime 配置决定。严禁对占用中的 0–2 卡做清理。
2. 显存是 `nvidia-smi memory.used` 的**设备全局值**；如果卡 3/4 还有别的进程，不能把全部显存算到 CLM。最好在独占窗口测试、配合容器/PID 指标。
3. **本地 CLM 与远程 Jev 不处于相同网络/硬件**，客户端 P95 对比包含跨地域 RTT、API 网关和限流；不能据此将延迟差全部归因于模型架构。
4. `--workers 1` 是低并发基线，之后分别运行 `--workers 4`、`--workers 8` 并为每次测试设置**不同的 `--name`**。同一输出名会自动续跑，不会重新跑完测试。
5. 质量评测应固定问题内容、顺序、模型版本和相同样本；对 API 做超时/429/5xx 重试，但不要把失败样本悄悄排除。
6. 测试所有模型前，应确保公网 Key 不泄露；对 Jev 调用可能产生费用，先用 `--limit 20` 小批量验证。
7. 数据集中的提示均为文本状态，不触发任何真实订单处理/删除/付款动作。

## 6. 如何扩展真正的公开数据集（推荐正式报告采用）

公开参考：[`Running-Dolphins/jev-bench`](https://github.com/Running-Dolphins/jev-bench)，覆盖 Banking77、CLINC150、MASSIVE、LEDGAR、AG News、BoolQ、SMS Spam、Quora duplicates、Yelp 等共 12 个任务，每任务默认 500 条。它为 Jev 给出了校准与分类方案，是外部基准参考，但需将**相同输入**送至 CLM 才能做真实配对比较。公开题可能与训练数据重合，请单列结果。项目源仓库没有直接附带所有数据文本，必须联网抓取并遵守各原始数据集许可。

本包额外提供无第三方依赖的 **Hugging Face 数据采集器**，支持：

```bash
# 600 条 BoolQ：真实文档 + yes/no 问题，Noul
python3 download_public.py --source boolq --count 600 --out data/public_boolq_600.jsonl

# 600 条 AG News：新闻主题四分类，Choice
python3 download_public.py --source ag_news --count 600 --out data/public_ag_news_600.jsonl

python3 benchmark.py validate --dataset data/public_boolq_600.jsonl
python3 benchmark.py run --dataset data/public_boolq_600.jsonl \
  --endpoint http://127.0.0.1:8700 --model clm-latest \
  --name clm_boolq --out results_boolq --workers 1
```

Hugging Face Dataset Viewer 每次最多 100 条，脚本按固定随机种子抽取跨页数据（不改变原始验证/测试划分），并跳过被服务器截断的行。由于原始素材**未在当前离线容器下载，联网采集步骤未做现场验证**；如 API 列名、配置或访问政策变化需按 Dataset Viewer 文档调整。BoolQ 为 CC BY-SA 3.0；AG News 官方页面标注 `unknown`，进一步传播前请核实其许可证。不同来源的数据请分别报告指标，不应直接与合成题成绩混为同一排名。

相关文档：

- CLM 项目与用法：<https://github.com/Contrastive-LM/CLM>
- Jev 系统接口：<https://docs.typesafe.ai/introduction>
- Jev 第三方开源评测：<https://github.com/Running-Dolphins/jev-bench>
- Hugging Face Dataset Viewer：<https://huggingface.co/docs/dataset-viewer/quick_start>
- BoolQ：<https://huggingface.co/datasets/google/boolq>
- AG News：<https://huggingface.co/datasets/fancyzhx/ag_news>

## 7. 开发自测（不访问真实 CLM / Jev）

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 模拟服务仅用于检验调用链，**不会产生任何有效模型性能结论**：
python3 tests/mock_systemone_server.py --port 18700
# 新开终端运行
python3 benchmark.py run --endpoint http://127.0.0.1:18700 \
  --model mock-test-model --name mock_transport_only --out mock_only \
  --limit 30 --workers 2 --warmup 0
```

## 8. 文件名要求

所有交付文件均采用 ASCII 英文字母、数字、下划线和标准扩展名。文件内部中文和 UTF-8 内容保留完整。CSV 带 UTF-8 BOM 可供 WPS / Excel 更稳定识别中文。
